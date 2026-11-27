"""Translate a catalog question into bounded search fields using local Ollama."""
import json
from urllib.request import Request, urlopen

from . import services


def search(question, category, config):
    question = services._text(question, "검색어", 200, True)
    categories = [r["category"] for r in services._read(
        "SELECT DISTINCT category FROM book WHERE category IS NOT NULL ORDER BY category LIMIT 100")]
    schema = {"type": "object", "properties": {
        "field": {"type": "string", "enum": ["any", "title", "author", "publisher", "isbn"]},
        "term": {"type": "string"}, "category": {"type": "string"},
        "available_only": {"type": "boolean"}},
        "required": ["field", "term", "category", "available_only"]}
    instruction = (
        "한국어 도서 검색문을 검색 조건으로 변환한다. 요청 문구와 조사를 제거한다. "
        "특정 책 제목은 title, 저자는 author, 출판사는 publisher, 모호한 핵심 단어는 any. "
        "분류 요청이면 category에 분류명만 넣고 term은 빈 문자열. 없는 조건은 빈 문자열. "
        "대출 가능한 책만 원할 때 available_only는 true. 원문의 고유명사와 숫자를 보존한다. "
        "예: 수학책 보여줘 => category 수학, term 빈 문자열. 처음 배우는 SQL은 어디 있어? => "
        "field title, term 처음 배우는 SQL. 저장된 분류: " + json.dumps(categories, ensure_ascii=False))
    body = {"model": config["OLLAMA_MODEL"], "stream": False, "format": schema,
            "system": instruction, "prompt": question, "options": {"temperature": 0, "num_predict": 180}}
    req = Request("http://127.0.0.1:11434/api/generate", data=json.dumps(body).encode(),
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=145) as response:
            answer = json.load(response)
        if not answer.get("done") or answer.get("done_reason") == "length":
            raise ValueError("incomplete")
        filters = json.loads(answer["response"])
        field, term, topic, available = [filters[k] for k in ("field","term","category","available_only")]
        if (field not in schema["properties"]["field"]["enum"] or not isinstance(available, bool)
                or not isinstance(term, str) or not isinstance(topic, str) or len(term)>200 or len(topic)>50):
            raise ValueError("invalid filters")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {"items": services.list_books(question, category),
                "notice": "AI 응답을 받지 못해 입력한 문구로 일반 검색했습니다."}
    if category and topic and category != topic:
        return {"items": [], "notice": "질문의 분류와 선택한 분류가 다릅니다."}
    # Some small models repeat a category as a title filter, e.g. 수학 + 수학책.
    if topic and term.replace(" ", "") in {topic, topic + "책", topic + "도서", topic + "분야"}:
        term, field = "", "any"
    if not (term or topic or available):
        term, field = question, "any"
    rows = services.list_books(term, category or topic, field=field, available_only=available)
    return {"items": rows, "notice": ""}
