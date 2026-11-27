"""Compare Simple/Enhanced prompts against references on one MySQL snapshot.

Examples (run from the project root):
  python scripts/evaluate_nl2sql.py --reference-only
  python scripts/evaluate_nl2sql.py --reference-date 2026-11-23
Real evaluation makes 24 model calls for the 12 included questions.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from library_app.config import settings
from library_app.nl2sql import (
    NL2SQLError, build_prompt, compare_results, execute_readonly, generate_sql,
    open_readonly_snapshot, reference_date,
)


def load_config():
    return settings()


def evaluate(config, questions, reference_only=False):
    fixed_date = reference_date(config)
    config = dict(config, REFERENCE_DATE=fixed_date)
    provider = config.get("LLM_PROVIDER", "openai")
    if not reference_only and provider == "openai" and not config.get("OPENAI_API_KEY"):
        raise NL2SQLError("실제 LLM 비교 평가에는 OPENAI_API_KEY가 필요합니다. 정답 SQL만 검증하려면 --reference-only를 사용하세요.", "missing_api_key")
    connection = open_readonly_snapshot(config)
    report = {
        "evaluation_type": "reference_only" if reference_only else f"live_{provider}_comparison",
        "llm_evaluated": not reference_only,
        "evaluated_at": datetime.now(timezone.utc).isoformat(), "reference_date": fixed_date,
        "model": config.get(provider.upper() + "_MODEL") if not reference_only else None,
        "dataset_sha256": hashlib.sha256(json.dumps(questions, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        "database_name": config["DB_NAME"],
        "snapshot": "MySQL REPEATABLE READ WITH CONSISTENT SNAPSHOT, READ ONLY",
        "limits": {"max_rows": 200, "max_execution_ms": 2000},
        "note": "정답 SQL 실행 검증이며 LLM 정확도 측정이 아님" if reference_only else "동일 스냅샷/동일 질문/동일 요청 모델로 두 프롬프트를 비교. 결과일치율은 이 데이터셋에 한정됨.",
        "results": [],
    }
    try:
        for question in questions:
            reference_sql = question["reference_sql"].format(reference_date=fixed_date)
            base = {k: question[k] for k in ["id", "level", "question", "intent", "ordered"]}
            base["reference_sql"] = reference_sql
            try:
                expected = execute_readonly(reference_sql, config, connection)
                base["reference_result"] = expected
                if expected["truncated"]:
                    raise NL2SQLError("정답 결과가 200행을 초과하여 이 데이터셋으로 평가할 수 없습니다.")
            except NL2SQLError as exc:
                raise NL2SQLError(f"정답 SQL {question['id']} 검증 실패: {exc}", "invalid_reference") from exc
            if reference_only:
                report["results"].append(dict(base, status="reference_executed", correct=None))
                continue
            # Deliberately no retries or corrections: one generation per prompt/question.
            for mode in ["simple", "enhanced"]:
                row = dict(base, prompt_mode=mode, prompt=build_prompt(question["question"], mode, fixed_date), correct=False)
                started = time.perf_counter()
                try:
                    generated = generate_sql(question["question"], mode, config)
                    row.update(generated)
                    actual = execute_readonly(generated["generated_sql"], config, connection)
                    row["actual_result"] = actual
                    correct, reason = compare_results(expected, actual, question["ordered"])
                    row.update(correct=correct, status="correct" if correct else "semantic_error", comparison=reason)
                except NL2SQLError as exc:
                    row.update(status="failed", error_code=exc.code, error=str(exc), comparison="SQL 생성·검증·실행 단계 실패")
                row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
                report["results"].append(row)
        if reference_only:
            report["summary"] = {"references_executed": len(report["results"]), "accuracy": None, "api_calls": 0}
        else:
            summary = {}
            for mode in ["simple", "enhanced"]:
                selected = [r for r in report["results"] if r["prompt_mode"] == mode]
                levels = {}
                for level in [1, 2, 3]:
                    subset = [r for r in selected if r["level"] == level]
                    levels[str(level)] = {"correct": sum(r["correct"] for r in subset), "total": len(subset)}
                summary[mode] = {
                    "correct": sum(r["correct"] for r in selected), "total": len(selected),
                    "execution_success": sum(r["status"] != "failed" for r in selected),
                    "accuracy": sum(r["correct"] for r in selected) / len(selected) if selected else None,
                    "mean_latency_ms": sum(r["elapsed_ms"] for r in selected) / len(selected) if selected else None,
                    "levels": levels,
                }
            report["summary"] = summary
        return report
    finally:
        connection.rollback()
        connection.close()


def write_report(report, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "reference_results" if not report["llm_evaluated"] else "llm_comparison"
    json_path = output_dir / (stem + ".json")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ["id", "level", "question", "prompt_mode", "model", "reference_sql", "generated_sql", "status", "correct", "comparison", "error", "elapsed_ms"]
    with (output_dir / (stem + ".csv")).open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["results"])
    return json_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-only", action="store_true", help="API 호출 없이 정답 SQL만 실행 검증")
    parser.add_argument("--reference-date", help="오늘의 기준일 YYYY-MM-DD. seed 기준일과 맞추세요.")
    parser.add_argument("--output-dir", default=str(ROOT / "evaluation" / "results"))
    parser.add_argument("--questions", default=str(ROOT / "evaluation" / "questions.json"))
    args = parser.parse_args(argv)
    config = load_config()
    if args.reference_date:
        config["REFERENCE_DATE"] = args.reference_date
    try:
        questions = json.loads(Path(args.questions).read_text(encoding="utf-8"))
        report = evaluate(config, questions, args.reference_only)
        path = write_report(report, args.output_dir)
        print(json.dumps({"saved": str(path), "type": report["evaluation_type"], "summary": report["summary"]}, ensure_ascii=False, indent=2))
        return 0
    except (NL2SQLError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
