"""실제 MySQL에 연결해 업무 규칙과 제약조건을 검증한다.

conftest.py의 app fixture가 매 테스트마다 별도 seed DB를 준비한다.
API 호출과 관계없이 SQL/트랜잭션 자체를 검증하는 통합 테스트이다.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pymysql
import pytest

from library_app import services
from library_app.db import connect, transaction
from library_app.seed import seed_database


def scalar(sql, args=()):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return next(iter(cur.fetchone().values()))
    finally:
        conn.close()


def new_member():
    return services.save_member({"name": "검증회원", "phone": "000-0000-9999"})


def new_copy(label=None):
    book = services.save_book({"title": "검증용 도서 " + uuid4().hex[:8]})
    return services.add_owned({"book_id": book["book_id"], "owned_id": label or "T"+uuid4().hex[:15], "location": "테스트 자료실"})


def test_sample_data_and_due_today_boundary(app):
    with app.app_context():
        stats = services.dashboard()
        assert stats == {"total_books": 18, "total_copies": 21, "available_copies": 13,
                         "active_loans": 6, "overdue_loans": 1, "total_members": 8}
        overdue = services.list_loans(filter="overdue")
        assert len(overdue) == 1
        assert overdue[0]["owned_id"] == "B003"
        assert overdue[0]["overdue_days"] == 6
        due_today = next(x for x in services.list_loans(filter="active") if x["owned_id"] == "B005")
        assert due_today["overdue_days"] == 0
        assert not due_today["is_overdue"]


def test_seed_cannot_overwrite_existing_data(app):
    with app.app_context():
        before = services.dashboard()
        with pytest.raises(ValueError, match="모두 비어"):
            seed_database()
        assert services.dashboard() == before


def test_optional_isbn_multiple_nulls_and_unique_valid_isbn(app):
    with app.app_context():
        first = services.save_book({"title": "ISBN 없는 도서 A"})
        second = services.save_book({"title": "ISBN 없는 도서 B", "isbn": ""})
        assert first["isbn"] is None and second["isbn"] is None
        # 표준 검사숫자가 유효한 예시 ISBN. 실제 책 메타데이터인 것처럼 사용하지 않는다.
        book = services.save_book({"title": "검증 전용 도서", "isbn": "978-0-306-40615-7"})
        assert book["isbn"] == "9780306406157"
        with pytest.raises(services.DomainError) as duplicate:
            services.save_book({"title": "중복 ISBN", "isbn": "9780306406157"})
        assert duplicate.value.code == "DUPLICATE"
        with pytest.raises(services.DomainError, match="검사 숫자"):
            services.save_book({"title": "오류 ISBN", "isbn": "9780306406158"})


def test_search_counts_are_not_inflated_by_past_loans(app):
    with app.app_context():
        row = next(b for b in services.list_books(query="데이터베이스") if b["book_id"] == 1)
        assert (row["total_count"], row["normal_count"], row["available_count"]) == (2, 2, 1)
        detail = services.book_detail(1)
        assert len(detail["copies"]) == 2
        assert detail["available_count"] == 1
        assert services.book_detail(18)["copies"] == []


def test_checkout_return_and_repeat_loan_keep_history(app):
    with app.app_context():
        mid = new_member()["member_id"]
        oid = new_copy()["owned_id"]
        loan = services.checkout(mid, oid)
        assert loan["open_flag"] == 1
        assert loan["due_date"] == services.today()+timedelta(days=app.config.get("LOAN_DAYS",14))
        with pytest.raises(services.DomainError) as duplicate:
            services.checkout(mid, oid)
        assert duplicate.value.code == "COPY_ON_LOAN"
        returned = services.return_loan(loan["loan_id"])
        assert returned["return_date"] == services.today()
        assert returned["open_flag"] is None
        with pytest.raises(services.DomainError) as repeated_return:
            services.return_loan(loan["loan_id"])
        assert repeated_return.value.code == "ALREADY_RETURNED"
        next_loan = services.checkout(mid, oid)
        assert next_loan["loan_id"] != loan["loan_id"]
        assert scalar("SELECT COUNT(*) FROM loan WHERE owned_id=%s", (oid,)) == 2


@pytest.mark.parametrize("member_id,owned_id,expected", [
    (2,"B002","MEMBER_OVERDUE"),
    (6,"B002","MEMBER_INACTIVE"),
    (7,"B002","MEMBER_INACTIVE"),
    (4,"B002","LOAN_LIMIT"),
    (8,"B020","COPY_UNAVAILABLE"),
    (8,"B021","COPY_UNAVAILABLE"),
    (8,"B001","COPY_ON_LOAN"),
])
def test_checkout_rejection_does_not_create_a_loan(app, member_id, owned_id, expected):
    with app.app_context():
        before = scalar("SELECT COUNT(*) FROM loan")
        with pytest.raises(services.DomainError) as error:
            services.checkout(member_id, owned_id)
        assert error.value.code == expected
        assert scalar("SELECT COUNT(*) FROM loan") == before


def test_due_today_member_can_borrow(app):
    with app.app_context():
        loan = services.checkout(3,"B002")
        assert loan["member_id"] == 3


def test_withdrawal_preserves_history_and_blocks_open_loans(app):
    with app.app_context():
        with pytest.raises(services.DomainError) as error:
            services.save_member({"status":"WITHDRAWN"},1)
        assert error.value.code == "OPEN_LOAN"
        previous = scalar("SELECT COUNT(*) FROM loan WHERE member_id=5")
        services.save_member({"status":"WITHDRAWN"},5)
        assert scalar("SELECT COUNT(*) FROM loan WHERE member_id=5") == previous


def test_copy_state_change_cannot_hide_active_loan(app):
    with app.app_context():
        with pytest.raises(services.DomainError) as error:
            services.update_owned("B001",{"status":"DISCARDED"})
        assert error.value.code == "OPEN_LOAN"
        assert services.update_owned("B002",{"status":"LOST"})["status"] == "LOST"
        assert services.book_detail(1)["available_count"] == 0


def test_account_is_unique_and_password_is_not_plaintext(app):
    with app.app_context():
        mid = new_member()["member_id"]
        account = services.create_account({"login_id":"new_member","password":"testpass123!","member_id":mid})
        assert "password_hash" not in account
        stored = scalar("SELECT password_hash FROM user_account WHERE account_id=%s",(account["account_id"],))
        assert stored != "testpass123!"
        with pytest.raises(services.DomainError) as duplicate:
            services.create_account({"login_id":"second_account","password":"testpass123!","member_id":mid})
        assert duplicate.value.code == "DUPLICATE"
        with pytest.raises(services.DomainError):
            services.create_account({"login_id":"wrong_admin","password":"testpass123!","role":"ADMIN","member_id":mid})


def test_foreign_key_and_generated_open_flag_are_database_constraints(app):
    with app.app_context():
        conn=connect()
        try:
            with conn.cursor() as cur:
                with pytest.raises(pymysql.IntegrityError):
                    cur.execute("INSERT INTO loan(member_id,owned_id,loan_date,due_date) VALUES (999999,'B002',%s,%s)",
                                (services.today(),services.today()+timedelta(days=14)))
                conn.rollback()
                with pytest.raises(pymysql.IntegrityError):
                    cur.execute("INSERT INTO loan(member_id,owned_id,loan_date,due_date) VALUES (8,'B001',%s,%s)",
                                (services.today(),services.today()+timedelta(days=14)))
                conn.rollback()
                with pytest.raises(pymysql.err.OperationalError):
                    cur.execute("INSERT INTO loan(member_id,owned_id,loan_date,due_date) VALUES (8,'B002',%s,%s)",
                                (services.today(),services.today()-timedelta(days=1)))
        finally:
            conn.rollback()
            conn.close()


def test_transaction_rolls_back_on_error(app):
    with app.app_context():
        title="롤백검증 "+uuid4().hex
        with pytest.raises(RuntimeError):
            with transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO book(title) VALUES (%s)",(title,))
                raise RuntimeError("검증용 작업 중단")
        assert scalar("SELECT COUNT(*) FROM book WHERE title=%s",(title,)) == 0


def test_two_members_cannot_checkout_same_copy_concurrently(app):
    with app.app_context():
        mids=[new_member()["member_id"],new_member()["member_id"]]
        oid=new_copy()["owned_id"]
    gate=Barrier(2)
    def run(mid):
        with app.app_context():
            gate.wait(timeout=5)
            try:
                services.checkout(mid,oid)
                return "OK"
            except services.DomainError as exc:
                return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,mids))
    assert sorted(results) == ["COPY_ON_LOAN","OK"]
    with app.app_context():
        assert scalar("SELECT COUNT(*) FROM loan WHERE owned_id=%s AND return_date IS NULL",(oid,)) == 1


def test_concurrent_requests_do_not_bypass_member_limit(app, monkeypatch):
    monkeypatch.setitem(app.config,"MAX_LOANS",1)
    with app.app_context():
        mid=new_member()["member_id"]
        oids=[new_copy()["owned_id"],new_copy()["owned_id"]]
    gate=Barrier(2)
    def run(oid):
        with app.app_context():
            gate.wait(timeout=5)
            try:
                services.checkout(mid,oid)
                return "OK"
            except services.DomainError as exc:
                return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,oids))
    assert sorted(results) == ["LOAN_LIMIT","OK"]
