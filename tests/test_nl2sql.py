"""Offline tests use labelled provider mocks, not measured OpenAI accuracy."""
import importlib.util
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from library_app import nl2sql as n


ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = json.loads((ROOT / "evaluation" / "questions.json").read_text("utf-8"))


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda q: q["id"])
def test_reference_sql_passes_validator(question):
    sql = question["reference_sql"].format(reference_date="2026-11-23")
    assert n.validate_sql(sql).startswith("SELECT")


@pytest.mark.parametrize("sql", [
    "DELETE FROM book", "UPDATE book SET title='x'", "DROP TABLE book",
    "SELECT title FROM book; DELETE FROM loan", "SHOW TABLES",
    "SELECT password_hash FROM user_account", "SELECT phone FROM member",
    "SELECT m.phone AS contact FROM member m", "SELECT * FROM member",
    "SELECT m.* FROM member m", "SELECT * FROM book", "SELECT @@version",
    "SELECT @x := 1", "SELECT SLEEP(5)", "SELECT BENCHMARK(100000,1+1)",
    "SELECT LOAD_FILE('/etc/passwd')", "SELECT my_custom_udf()",
    "SELECT GET_LOCK('x', 5)", "SELECT USER()", "SELECT VERSION()",
    "SELECT table_name FROM information_schema.tables",
    "SELECT title FROM other_db.book", "SELECT title FROM book FOR UPDATE",
    "SELECT title FROM book INTO OUTFILE '/tmp/secret'",
    "SELECT /*+ MAX_EXECUTION_TIME(900000) */ title FROM book",
    "SELECT title FROM book -- comment", "SELECT title FROM book # comment",
    "WITH t AS (SELECT title FROM book) SELECT title FROM t",
    "SELECT title FROM book UNION SELECT name FROM member",
    "SELECT random_unknown_column FROM book", "SELECT m.isbn FROM member m",
    "SELECT title FROM book LIMIT -1", "SELECT title FROM book LIMIT 1 OFFSET 10001",
    "```sql\nSELECT title FROM book\n```", "", "x" * 12001,
])
def test_unsafe_or_unsupported_sql_rejected(sql):
    with pytest.raises(n.NL2SQLError):
        n.validate_sql(sql)


def test_count_star_allowed_without_exposing_row_columns():
    assert n.validate_sql("SELECT COUNT(*) AS total FROM member")


def test_prompt_comparison_varies_only_schema_enrichment():
    simple = n.build_prompt("대출 가능한 책 수", "simple", "2026-11-23")
    enhanced = n.build_prompt("대출 가능한 책 수", "enhanced", "2026-11-23")
    assert "Relationships and meanings" not in simple
    assert "Relationships and meanings" in enhanced
    assert "'2026-11-23'" in simple and "'2026-11-23'" in enhanced
    assert "phone" not in simple and "password_hash" not in simple
    assert "COUNT" not in simple  # No reference answers leak into the schema-only prompt.


def test_missing_key_does_not_contact_provider(monkeypatch):
    monkeypatch.setattr(n, "urlopen", lambda *a, **kw: pytest.fail("network call without key"))
    with pytest.raises(n.NL2SQLError, match="OPENAI_API_KEY"):
        n.generate_sql("도서 수", config={})


class MockResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return json.dumps(self.payload).encode()


def test_mock_provider_extracts_actual_response_text(monkeypatch):
    captured = {}

    def mock_urlopen(request, timeout):
        captured.update(json.loads(request.data))
        assert request.full_url == "https://api.openai.com/v1/responses"
        return MockResponse({
            "id": "mock_response_id", "status": "completed", "model": "mock-model",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "SELECT COUNT(*) AS total FROM book"}]}],
        })

    monkeypatch.setattr(n, "urlopen", mock_urlopen)
    result = n.generate_sql("도서 수", config={"OPENAI_API_KEY": "test-only-key", "REFERENCE_DATE": "2026-11-23"})
    assert result["generated_sql"] == "SELECT COUNT(*) AS total FROM book"
    assert result["model"] == "mock-model"
    assert captured["store"] is False
    assert "test-only-key" not in json.dumps(captured)


def test_mock_incomplete_provider_never_executes_partial_sql(monkeypatch):
    monkeypatch.setattr(n, "urlopen", lambda *a, **kw: MockResponse({"status": "incomplete", "output": []}))
    with pytest.raises(n.NL2SQLError) as exc:
        n.generate_sql("도서 수", config={"OPENAI_API_KEY": "test-only-key"})
    assert exc.value.code == "incomplete_response"


def test_mock_provider_http_error_is_redacted(monkeypatch):
    def failing(*args, **kwargs):
        raise HTTPError("https://api.openai.com/v1/responses", 401, "secret content", {}, None)
    monkeypatch.setattr(n, "urlopen", failing)
    with pytest.raises(n.NL2SQLError) as exc:
        n.generate_sql("도서 수", config={"OPENAI_API_KEY": "test-only-key"})
    assert "secret content" not in str(exc.value)
    assert "401" in str(exc.value)


@pytest.mark.parametrize("config", [
    {}, {"DB_READ_USER": "root", "DB_READ_PASSWORD": "x"},
    {"DB_USER": "app", "DB_READ_USER": "app", "DB_READ_PASSWORD": "x"},
    {"DB_READ_USER": "reader", "DB_READ_PASSWORD": ""},
])
def test_readonly_credentials_never_fall_back_to_writer(config):
    with pytest.raises(n.NL2SQLError) as exc:
        n.open_readonly_snapshot(config)
    assert exc.value.code == "readonly_configuration"


def result(values, columns=("value",), types=None):
    obj = {"columns": list(columns), "rows": [dict(zip(columns, row)) for row in values], "truncated": False}
    if types:
        obj["column_types"] = types
    return obj


def test_comparison_preserves_duplicate_rows():
    assert not n.compare_results(result([(1,), (1,)]), result([(1,)]))[0]


def test_comparison_order_depends_on_question():
    a, b = result([(1,), (2,)]), result([(2,), (1,)])
    assert n.compare_results(a, b, ordered=False)[0]
    assert not n.compare_results(a, b, ordered=True)[0]


def test_comparison_aliases_and_numeric_types():
    a = result([(2,)], ("total",), [8])
    b = result([("2.000",)], ("count",), [246])
    assert n.compare_results(a, b)[0]


def test_comparison_rejects_partial_or_shape_mismatch():
    a = result([(1,)])
    b = dict(a, truncated=True)
    assert not n.compare_results(a, b)[0]
    assert not n.compare_results(result([], ("id",)), result([], ("id", "title")))[0]


class MockCursor:
    description = [("book_id", 3)]

    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql):
        self.connection.executed.append(sql)

    def fetchmany(self, count):
        return [(i,) for i in range(201)]


class MockConnection:
    def __init__(self):
        self.executed = []

    def cursor(self, *args):
        return MockCursor(self)


def test_execute_caps_rows_and_preserves_shared_snapshot():
    connection = MockConnection()
    r = n.execute_readonly("SELECT book_id FROM book LIMIT 9999", config={}, connection=connection)
    assert r["row_count"] == 200 and r["truncated"] is True
    assert connection.executed == ["SELECT book_id FROM book LIMIT 201"]


def test_execute_rewrites_today_for_reproducible_evaluation():
    connection = MockConnection()
    n.execute_readonly("SELECT loan_id FROM loan WHERE due_date < CURDATE()", config={"REFERENCE_DATE": "2026-11-23"}, connection=connection)
    assert "CAST('2026-11-23' AS DATE)" in connection.executed[0]
    assert "CURDATE" not in connection.executed[0]


def test_evaluator_counts_semantic_and_generation_failures(monkeypatch):
    spec = importlib.util.spec_from_file_location("evaluate_nl2sql_test", ROOT / "scripts" / "evaluate_nl2sql.py")
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)
    class Snapshot:
        def rollback(self): pass
        def close(self): pass
    monkeypatch.setattr(evaluator, "open_readonly_snapshot", lambda config: Snapshot())
    monkeypatch.setattr(evaluator, "execute_readonly", lambda sql, *a: result([(1,)] if "reference" in sql else [(2,)]))
    def generate(question, mode, config):
        if mode == "simple":
            raise n.NL2SQLError("mock generation failure", "provider_error")
        return {"generated_sql": "SELECT 2 AS actual", "model": "mock-only"}
    monkeypatch.setattr(evaluator, "generate_sql", generate)
    q = {"id": "test", "level": 1, "question": "테스트", "intent": "mock only", "ordered": False, "reference_sql": "SELECT 1 AS reference"}
    report = evaluator.evaluate({"OPENAI_API_KEY": "mock-only", "DB_NAME": "mock", "REFERENCE_DATE": "2026-11-23"}, [q])
    assert report["summary"]["simple"]["total"] == 1
    assert report["summary"]["simple"]["accuracy"] == 0
    assert report["results"][1]["status"] == "semantic_error"
    assert report["summary"]["enhanced"]["accuracy"] == 0


def test_evaluator_reference_only_never_reports_accuracy(monkeypatch):
    spec = importlib.util.spec_from_file_location("evaluate_nl2sql_reference_test", ROOT / "scripts" / "evaluate_nl2sql.py")
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)
    class Snapshot:
        def rollback(self): pass
        def close(self): pass
    monkeypatch.setattr(evaluator, "open_readonly_snapshot", lambda config: Snapshot())
    monkeypatch.setattr(evaluator, "execute_readonly", lambda *a: result([(1,)]))
    monkeypatch.setattr(evaluator, "generate_sql", lambda *a: pytest.fail("reference-only must not call model"))
    q = {"id": "test", "level": 1, "question": "테스트", "intent": "mock only", "ordered": False, "reference_sql": "SELECT 1 AS reference"}
    report = evaluator.evaluate({"DB_NAME": "mock", "REFERENCE_DATE": "2026-11-23"}, [q], reference_only=True)
    assert report["llm_evaluated"] is False
    assert report["summary"]["accuracy"] is None
    assert report["summary"]["api_calls"] == 0

@pytest.mark.parametrize("done_reason", ["stop", "length"])
def test_ollama_uses_local_model_without_api_key(monkeypatch, done_reason):
    from io import BytesIO

    def reply(request, timeout):
        assert request.full_url == "http://127.0.0.1:11434/api/generate"
        assert not request.has_header("Authorization")
        payload = json.loads(request.data)
        assert payload["model"] == "course-model"
        assert "수학책" in payload["prompt"]
        assert payload["stream"] is False
        body = {"done": True, "done_reason": done_reason, "model": "course-model",
                "response": "```sql\nSELECT title FROM book\n```"}
        return BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(n, "urlopen", reply)
    config = {"LLM_PROVIDER": "ollama", "OLLAMA_MODEL": "course-model"}
    if done_reason == "length":
        with pytest.raises(n.NL2SQLError, match="완료"):
            n.generate_sql("수학책", config=config)
    else:
        result = n.generate_sql("수학책", config=config)
        assert result["model"] == "course-model"
        assert n.validate_sql(result["generated_sql"]) == "SELECT title FROM book"


def test_focused_schema_for_single_entity_preserves_multitable_queries():
    date = "2026-11-23"
    book_only = n.build_prompt("출판사가 가상공학사인 책의 제목을 보여줘", "enhanced", date)
    assert "book(book_id " in book_only and "owned_book(owned_id " not in book_only
    for question in ["가상공학사가 출판한 책 중 빌릴 수 있는 책은?", "자료구조는 어디 있어?"]:
        prompt = n.build_prompt(question, "enhanced", date)
        assert "book(book_id " in prompt and "owned_book(owned_id " in prompt
