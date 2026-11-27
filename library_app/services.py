"""도서관 업무 규칙. HTTP와 분리해 같은 로직을 테스트에서 호출한다."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import re

import pymysql
from flask import current_app
from werkzeug.security import generate_password_hash

from .db import connect, transaction


class DomainError(ValueError):
    def __init__(self, message, status_code=400, code="VALIDATION_ERROR"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def today():
    """평가 재현 시 REFERENCE_DATE를 고정할 수 있다."""
    value = current_app.config.get("REFERENCE_DATE")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value) if value else datetime.now(timezone(timedelta(hours=9))).date()


def _text(value, label, maximum, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise DomainError(f"{label}은(는) 문자열로 입력해 주세요.")
    value = value.strip()
    if required and not value:
        raise DomainError(f"{label}을(를) 입력해 주세요.")
    if len(value) > maximum:
        raise DomainError(f"{label}은(는) {maximum}자 이내로 입력해 주세요.")
    return value or None


def _identifier(value, label):
    if isinstance(value, bool):
        raise DomainError(f"{label}이 올바르지 않습니다.")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise DomainError(f"{label}이 올바르지 않습니다.") from None
    if number <= 0 or str(value).strip() != str(number):
        raise DomainError(f"{label}이 올바르지 않습니다.")
    return number


def _date(value, label, required=False):
    if not value:
        if required:
            raise DomainError(f"{label}을(를) 입력해 주세요.")
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise DomainError(f"{label}은(는) YYYY-MM-DD 형식이어야 합니다.") from None


def _status(value, allowed, label):
    if value not in allowed:
        raise DomainError(f"{label}은(는) {', '.join(allowed)} 중 하나여야 합니다.")
    return value


@contextmanager
def _write():
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                yield cur
    except pymysql.IntegrityError as exc:
        if exc.args[0] == 1062:
            raise DomainError("이미 등록된 ISBN, 관리번호 또는 계정입니다.", 409, "DUPLICATE") from exc
        raise DomainError("연결된 데이터 또는 입력값의 제약조건을 확인해 주세요.", 409, "CONSTRAINT") from exc


def _read(sql, params=(), one=False):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone() if one else list(cur.fetchall())
    finally:
        conn.close()


def _require(row, label):
    if not row:
        raise DomainError(f"{label}을(를) 찾을 수 없습니다.", 404, "NOT_FOUND")
    return row


def list_books(query="", category="", *, available_only=False, field="any"):
    query = _text(query, "검색어", 200) or ""
    category = _text(category, "분류", 50) or ""
    columns = {"title": "b.title", "author": "b.author", "publisher": "b.publisher", "isbn": "b.isbn"}
    if field not in {*columns, "any"}:
        raise DomainError("검색 필드가 올바르지 않습니다.")
    targets = columns.values() if field == "any" else [columns[field]]
    search_conditions = " OR ".join(col + " LIKE %s" for col in targets)
    terms = [f"%{query}%"] * len(targets)
    return _read(f"""
        SELECT b.*, COALESCE(c.total_count,0) AS total_count,
               COALESCE(c.normal_count,0) AS normal_count,
               COALESCE(c.available_count,0) AS available_count
        FROM book b LEFT JOIN (
            SELECT o.book_id, COUNT(*) AS total_count,
                   SUM(o.status='NORMAL') AS normal_count,
                   SUM(o.status='NORMAL' AND l.loan_id IS NULL) AS available_count
            FROM owned_book o LEFT JOIN loan l
              ON l.owned_id=o.owned_id AND l.return_date IS NULL
            GROUP BY o.book_id
        ) c ON c.book_id=b.book_id
        WHERE (%s='' OR {search_conditions})
          AND (%s=0 OR c.available_count>0)
          AND (%s='' OR b.category=%s)
        ORDER BY b.book_id DESC
        LIMIT 500
    """, (query, *terms, bool(available_only), category, category))


def book_detail(book_id, viewer_id=None):
    book_id = _identifier(book_id, "도서번호")
    row = _require(_read("SELECT * FROM book WHERE book_id=%s", (book_id,), True), "도서")
    row["copies"] = _read("""
        SELECT o.*, l.loan_id AS active_loan_id,
               EXISTS (SELECT 1 FROM loan_request r WHERE r.member_id=%s AND r.owned_id=o.owned_id
                       AND r.status='PENDING') AS requested,
               (o.status='NORMAL' AND l.loan_id IS NULL) AS available
        FROM owned_book o LEFT JOIN loan l
          ON l.owned_id=o.owned_id AND l.return_date IS NULL
        WHERE o.book_id=%s ORDER BY o.owned_id
    """, (viewer_id,book_id))
    row["total_count"] = len(row["copies"])
    row["normal_count"] = sum(c["status"] == "NORMAL" for c in row["copies"])
    row["available_count"] = sum(bool(c["available"]) for c in row["copies"])
    return row


def save_book(payload, book_id=None):
    if not isinstance(payload, dict):
        raise DomainError("도서 입력값을 확인해 주세요.")
    title = _text(payload.get("title"), "제목", 200, True)
    isbn = _text(payload.get("isbn"), "ISBN", 30)
    if isbn:
        isbn = isbn.replace("-", "").replace(" ", "")
        if not re.fullmatch(r"[0-9]{13}", isbn):
            raise DomainError("ISBN은 13자리 숫자로 입력하거나 비워 두세요.")
        total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(isbn[:12]))
        if (10 - total % 10) % 10 != int(isbn[-1]):
            raise DomainError("ISBN 검사 숫자가 올바르지 않습니다.")
    fields = {
        "isbn": isbn, "title": title,
        "author": _text(payload.get("author"), "저자", 100),
        "publisher": _text(payload.get("publisher"), "출판사", 100),
        "pub_date": _date(payload.get("pub_date"), "출판일"),
        "category": _text(payload.get("category"), "분류", 50),
    }
    # 외부 도서 등록 경로에서도 동일한 제약조건으로 저장한다.
    if "metadata_source" in payload:
        fields["metadata_source"] = _text(payload["metadata_source"], "출처", 30, True)
    if "source_url" in payload:
        fields["source_url"] = _text(payload["source_url"], "출처 URL", 500)
        if fields["source_url"] and not fields["source_url"].startswith(("https://", "http://")):
            raise DomainError("출처 URL은 http 또는 https 주소여야 합니다.")
    if payload.get("metadata_source") == "NL_ISBN":
        fields["imported_at"] = datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None, microsecond=0)
    with _write() as cur:
        if book_id is None:
            columns = ",".join(fields)
            cur.execute(f"INSERT INTO book ({columns}) VALUES ({','.join(['%s']*len(fields))})", tuple(fields.values()))
            book_id = cur.lastrowid
        else:
            book_id = _identifier(book_id, "도서번호")
            cur.execute("SELECT book_id FROM book WHERE book_id=%s FOR UPDATE", (book_id,))
            _require(cur.fetchone(), "도서")
            cur.execute(f"UPDATE book SET {','.join(k+'=%s' for k in fields)} WHERE book_id=%s", (*fields.values(), book_id))
        cur.execute("SELECT * FROM book WHERE book_id=%s", (book_id,))
        return cur.fetchone()


def add_owned(payload):
    owned_id = _text(payload.get("owned_id"), "관리번호", 20, True)
    book_id = _identifier(payload.get("book_id"), "도서번호")
    location = _text(payload.get("location"), "보관 위치", 50, True)
    status = _status(payload.get("status", "NORMAL"), ("NORMAL", "LOST", "DISCARDED"), "소장 상태")
    with _write() as cur:
        cur.execute("SELECT book_id FROM book WHERE book_id=%s", (book_id,))
        _require(cur.fetchone(), "도서")
        cur.execute("INSERT INTO owned_book (owned_id,book_id,location,status) VALUES (%s,%s,%s,%s)", (owned_id, book_id, location, status))
        return {"owned_id": owned_id, "book_id": book_id, "location": location, "status": status}


def update_owned(owned_id, payload):
    owned_id = _text(owned_id, "관리번호", 20, True)
    with _write() as cur:
        cur.execute("SELECT * FROM owned_book WHERE owned_id=%s FOR UPDATE", (owned_id,))
        row = _require(cur.fetchone(), "소장 도서")
        status = _status(payload.get("status", row["status"]), ("NORMAL", "LOST", "DISCARDED"), "소장 상태")
        location = _text(payload.get("location", row["location"]), "보관 위치", 50, True)
        if status != "NORMAL":
            cur.execute("SELECT loan_id FROM loan WHERE owned_id=%s AND return_date IS NULL", (owned_id,))
            if cur.fetchone():
                raise DomainError("미반납 대출이 있는 책은 먼저 반납 처리한 뒤 분실·폐기 상태로 변경해 주세요.", 409, "OPEN_LOAN")
        cur.execute("UPDATE owned_book SET location=%s,status=%s WHERE owned_id=%s", (location, status, owned_id))
        row.update(location=location, status=status)
        return row


def list_members(query=""):
    query = _text(query, "검색어", 100) or ""
    return _read("""
        SELECT m.*, a.account_id, COALESCE(l.active_loans,0) AS active_loans,
               COALESCE(l.overdue_loans,0) AS overdue_loans
        FROM member m LEFT JOIN user_account a ON a.member_id=m.member_id LEFT JOIN (
            SELECT member_id, COUNT(*) AS active_loans, SUM(due_date < %s) AS overdue_loans
            FROM loan WHERE return_date IS NULL GROUP BY member_id
        ) l ON l.member_id=m.member_id
        WHERE (%s='' OR m.name LIKE %s OR CAST(m.member_id AS CHAR)=%s)
        ORDER BY m.member_id LIMIT 500
    """, (today(), query, f"%{query}%", query))


def save_member(payload, member_id=None):
    with _write() as cur:
        existing = {}
        if member_id is not None:
            member_id = _identifier(member_id, "회원번호")
            cur.execute("SELECT * FROM member WHERE member_id=%s FOR UPDATE", (member_id,))
            existing = _require(cur.fetchone(), "회원")
        name = _text(payload.get("name", existing.get("name")), "이름", 50, True)
        phone = _text(payload.get("phone", existing.get("phone")), "연락처", 20, True)
        status = _status(payload.get("status", existing.get("status", "ACTIVE")), ("ACTIVE", "SUSPENDED", "WITHDRAWN"), "회원 상태")
        joined = _date(payload.get("join_date", existing.get("join_date", today())), "가입일", True)
        if joined > today():
            raise DomainError("가입일은 기준일보다 늦을 수 없습니다.")
        if member_id is not None and status == "WITHDRAWN":
            cur.execute("SELECT loan_id FROM loan WHERE member_id=%s AND return_date IS NULL LIMIT 1", (member_id,))
            if cur.fetchone():
                raise DomainError("미반납 도서가 있는 회원은 탈퇴할 수 없습니다.", 409, "OPEN_LOAN")
        if member_id is None:
            cur.execute("INSERT INTO member (name,phone,join_date,status) VALUES (%s,%s,%s,%s)", (name, phone, joined, status))
            member_id = cur.lastrowid
        else:
            cur.execute("UPDATE member SET name=%s,phone=%s,join_date=%s,status=%s WHERE member_id=%s", (name, phone, joined, status, member_id))
        return {"member_id": member_id, "name": name, "phone": phone, "join_date": joined, "status": status}


def list_loans(member_id=None, filter="all"):
    if filter not in ("all", "active", "returned", "overdue"):
        raise DomainError("대출 조회 조건이 올바르지 않습니다.")
    conditions = []
    params = [today(), today()]
    if member_id is not None:
        conditions.append("l.member_id=%s")
        params.append(_identifier(member_id, "회원번호"))
    if filter in ("active", "overdue"):
        conditions.append("l.return_date IS NULL")
    if filter == "returned":
        conditions.append("l.return_date IS NOT NULL")
    if filter == "overdue":
        conditions.append("l.due_date < %s")
        params.append(today())
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    return _read("""
        SELECT l.*, m.name AS member_name, b.title, o.location,
               (l.return_date IS NULL AND l.due_date < %s) AS is_overdue,
               CASE WHEN l.return_date IS NULL THEN GREATEST(DATEDIFF(%s,l.due_date),0)
                    ELSE GREATEST(DATEDIFF(l.return_date,l.due_date),0) END AS overdue_days
        FROM loan l JOIN member m ON m.member_id=l.member_id
        JOIN owned_book o ON o.owned_id=l.owned_id JOIN book b ON b.book_id=o.book_id
    """ + where + " ORDER BY l.loan_id DESC LIMIT 500", tuple(params))


def _check_borrowable(cur, member_id, owned_id):
    maximum = int(current_app.config.get("MAX_LOANS", 3))
    days = int(current_app.config.get("LOAN_DAYS", 14))
    if maximum < 1 or days < 1:
        raise DomainError("대출 정책 설정을 확인해 주세요.", 500, "CONFIG_ERROR")
    base = today()
    # 동일 회원의 동시 요청을 직렬화하여 대출 한도 우회를 막는다.
    cur.execute("SELECT * FROM member WHERE member_id=%s FOR UPDATE", (member_id,))
    member = _require(cur.fetchone(), "회원")
    if member["status"] != "ACTIVE":
        raise DomainError("정상 상태의 회원만 대출할 수 있습니다.", 409, "MEMBER_INACTIVE")
    cur.execute("SELECT COUNT(*) AS n, COALESCE(SUM(due_date < %s),0) AS overdue FROM loan WHERE member_id=%s AND return_date IS NULL", (base, member_id))
    counts = cur.fetchone()
    if counts["overdue"]:
        raise DomainError("연체 중인 도서가 있어 대출할 수 없습니다.", 409, "MEMBER_OVERDUE")
    if counts["n"] >= maximum:
        raise DomainError(f"동시에 최대 {maximum}권까지 대출할 수 있습니다.", 409, "LOAN_LIMIT")
    cur.execute("SELECT * FROM owned_book WHERE owned_id=%s FOR UPDATE", (owned_id,))
    owned = _require(cur.fetchone(), "소장 도서")
    if owned["status"] != "NORMAL":
        raise DomainError("분실 또는 폐기 상태의 책은 대출할 수 없습니다.", 409, "COPY_UNAVAILABLE")
    # locking read를 사용해, 다른 회원이 직전에 대출한 상태도 읽는다.
    cur.execute("SELECT loan_id FROM loan WHERE owned_id=%s AND return_date IS NULL FOR UPDATE", (owned_id,))
    if cur.fetchone():
        raise DomainError("이미 대출 중인 책입니다.", 409, "COPY_ON_LOAN")
    return base, days


def request_loan(member_id, owned_id):
    owned_id = _text(owned_id, "관리번호", 20, True)
    with _write() as cur:
        _check_borrowable(cur, member_id, owned_id)
        cur.execute("SELECT request_id FROM loan_request WHERE member_id=%s AND owned_id=%s AND status='PENDING' FOR UPDATE", (member_id, owned_id))
        if cur.fetchone():
            raise DomainError("이미 신청한 소장본입니다.", 409, "ALREADY_REQUESTED")
        cur.execute("INSERT INTO loan_request (member_id,owned_id) VALUES (%s,%s)", (member_id, owned_id))
        return {"request_id": cur.lastrowid, "status": "PENDING"}


def list_requests(member_id=None):
    return _read("""
        SELECT r.request_id,r.member_id,r.owned_id,r.status,r.requested_at,b.title,m.name AS member_name
        FROM loan_request r JOIN member m ON m.member_id=r.member_id
        JOIN owned_book o ON r.owned_id=o.owned_id JOIN book b ON o.book_id=b.book_id
        WHERE (%s IS NULL OR r.member_id=%s)
        ORDER BY (r.status='PENDING') DESC,r.request_id DESC LIMIT 500
    """, (member_id,member_id))


def decide_request(request_id, decision):
    if decision == "approve":
        row = _require(_read("SELECT member_id,owned_id FROM loan_request WHERE request_id=%s", (request_id,), True), "신청")
        return checkout(row["member_id"], row["owned_id"], request_id=request_id)
    if decision != "decline":
        raise DomainError("신청 처리 방식을 확인해 주세요.")
    with _write() as cur:
        cur.execute("UPDATE loan_request SET status='DECLINED' WHERE request_id=%s AND status='PENDING'", (request_id,))
        if cur.rowcount != 1:
            raise DomainError("대기 중인 신청이 없습니다.", 409)
        return {"status": "DECLINED"}


def checkout(member_id, owned_id, *, request_id=None):
    member_id = _identifier(member_id, "회원번호")
    owned_id = _text(owned_id, "관리번호", 20, True)
    with _write() as cur:
        base, days = _check_borrowable(cur, member_id, owned_id)
        cur.execute("""UPDATE loan_request SET status='APPROVED'
                       WHERE member_id=%s AND owned_id=%s AND status='PENDING'
                       AND (%s IS NULL OR request_id=%s)""", (member_id, owned_id, request_id, request_id))
        if request_id is not None and cur.rowcount != 1:
            raise DomainError("이미 처리된 대출 신청입니다.", 409)
        cur.execute("UPDATE loan_request SET status='DECLINED' WHERE owned_id=%s AND status='PENDING'", (owned_id,))
        cur.execute("INSERT INTO loan (member_id,owned_id,loan_date,due_date) VALUES (%s,%s,%s,%s)", (member_id, owned_id, base, base + timedelta(days=days)))
        cur.execute("SELECT * FROM loan WHERE loan_id=%s", (cur.lastrowid,))
        return cur.fetchone()


def return_loan(loan_id):
    loan_id = _identifier(loan_id, "대출번호")
    with _write() as cur:
        cur.execute("SELECT * FROM loan WHERE loan_id=%s", (loan_id,))
        initial = _require(cur.fetchone(), "대출 내역")
        # 대출과 같은 잠금 순서(회원 -> 소장 도서 -> 대출 내역)를 따른다.
        cur.execute("SELECT member_id FROM member WHERE member_id=%s FOR UPDATE", (initial["member_id"],))
        cur.fetchone()
        cur.execute("SELECT owned_id FROM owned_book WHERE owned_id=%s FOR UPDATE", (initial["owned_id"],))
        cur.fetchone()
        cur.execute("SELECT * FROM loan WHERE loan_id=%s FOR UPDATE", (loan_id,))
        row = cur.fetchone()
        if row["return_date"] is not None:
            raise DomainError("이미 반납 처리한 대출입니다.", 409, "ALREADY_RETURNED")
        if today() < row["loan_date"]:
            raise DomainError("반납일이 대출일보다 빠를 수 없습니다.")
        cur.execute("UPDATE loan SET return_date=%s WHERE loan_id=%s", (today(), loan_id))
        cur.execute("SELECT * FROM loan WHERE loan_id=%s", (loan_id,))
        return cur.fetchone()


def dashboard(member_id=None):
    base = today()
    if member_id is None:
        return _read("""
            SELECT (SELECT COUNT(*) FROM book) AS total_books,
                   (SELECT COUNT(*) FROM owned_book) AS total_copies,
                   (SELECT COUNT(*) FROM owned_book o WHERE o.status='NORMAL'
                      AND NOT EXISTS (SELECT 1 FROM loan l WHERE l.owned_id=o.owned_id AND l.return_date IS NULL)) AS available_copies,
                   (SELECT COUNT(*) FROM loan WHERE return_date IS NULL) AS active_loans,
                   (SELECT COUNT(*) FROM loan WHERE return_date IS NULL AND due_date < %s) AS overdue_loans,
                   (SELECT COUNT(*) FROM member) AS total_members
        """, (base,), True)
    member_id = _identifier(member_id, "회원번호")
    return _read("""
        SELECT COUNT(*) AS total_loans,
               COALESCE(SUM(return_date IS NULL),0) AS active_loans,
               COALESCE(SUM(return_date IS NULL AND due_date < %s),0) AS overdue_loans,
               COALESCE(SUM(return_date IS NOT NULL),0) AS returned_loans
        FROM loan WHERE member_id=%s
    """, (base, member_id), True)


def _credentials(payload):
    login_id = _text(payload.get("login_id"), "로그인 ID", 30, True)
    password = payload.get("password")
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise DomainError("비밀번호는 8~128자로 입력해 주세요.")
    return login_id, generate_password_hash(password)


def _insert_account(cur, login_id, password_hash, role, member_id):
    cur.execute("INSERT INTO user_account (login_id,password_hash,role,member_id) VALUES (%s,%s,%s,%s)",
                (login_id, password_hash, role, member_id))
    return {"account_id": cur.lastrowid, "login_id": login_id, "role": role, "member_id": member_id}


def register_member(payload):
    name = _text(payload.get("name"), "이름", 50, True)
    phone = _text(payload.get("phone"), "연락처", 20, True)
    login_id, password_hash = _credentials(payload)
    try:
        with _write() as cur:
            cur.execute("INSERT INTO member (name,phone,join_date,status) VALUES (%s,%s,%s,'ACTIVE')",
                        (name, phone, today()))
            _insert_account(cur, login_id, password_hash, "MEMBER", cur.lastrowid)
    except DomainError as exc:
        if exc.code == "DUPLICATE":
            raise DomainError("이미 사용 중인 아이디입니다.", 409, "DUPLICATE") from exc
        raise
    return {"login_id": login_id}


def create_account(payload):
    login_id, password_hash = _credentials(payload)
    role = _status(payload.get("role", "MEMBER"), ("ADMIN", "MEMBER"), "권한")
    member_id = payload.get("member_id")
    if role == "MEMBER":
        member_id = _identifier(member_id, "회원번호")
    elif member_id not in (None, ""):
        raise DomainError("관리자 계정에는 회원번호를 연결하지 않습니다.")
    else:
        member_id = None
    with _write() as cur:
        if member_id is not None:
            cur.execute("SELECT status FROM member WHERE member_id=%s FOR UPDATE", (member_id,))
            row = _require(cur.fetchone(), "회원")
            if row["status"] == "WITHDRAWN":
                raise DomainError("탈퇴 회원에게는 계정을 발급할 수 없습니다.")
        return _insert_account(cur, login_id, password_hash, role, member_id)
