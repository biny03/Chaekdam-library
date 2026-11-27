"""수업 시연용 가상 데이터. 공주대 소장자료/API 수집 결과가 아니다."""
from datetime import date, datetime, timedelta, timezone

from flask import current_app
from werkzeug.security import generate_password_hash

from .db import transaction


BOOKS = [
    ("데이터베이스 설계 실습", "김데이터", "가상교육출판", "2024-03-01", "컴퓨터"),
    ("처음 배우는 SQL", "이쿼리", "가상교육출판", "2023-09-10", "컴퓨터"),
    ("자바 프로그래밍 워크북", "박코딩", "가상공학사", "2024-02-20", "컴퓨터"),
    ("파이썬으로 만드는 웹 서비스", "최서버", "가상공학사", "2025-01-15", "컴퓨터"),
    ("컴퓨터 네트워크의 이해", "정통신", "가상공학사", "2022-06-01", "컴퓨터"),
    ("자료구조와 알고리즘 노트", "윤구조", "가상교육출판", "2025-03-01", "컴퓨터"),
    ("인공지능과 일상의 질문", "한지능", "가상지식사", "2025-05-01", "컴퓨터"),
    ("공간을 읽는 기하학", "김도형", "가상지식사", "2021-09-15", "수학"),
    ("생활 속 통계 이야기", "이통계", "가상지식사", "2024-08-15", "수학"),
    ("대학생을 위한 글쓰기", "박문장", "가상인문사", "2023-02-10", "인문"),
    ("우리 동네의 역사", "최기록", "가상인문사", "2022-05-01", "역사"),
    ("작은 도서관의 하루", "정책장", "가상문학사", "2025-04-01", "문학"),
    ("별빛이 머무는 정원", "윤정원", "가상문학사", "2024-10-01", "문학"),
    ("경제를 배우는 첫 수업", "한경제", "가상지식사", "2024-01-20", "경제"),
    ("함께 사는 도시", "김공동", "가상인문사", "2025-06-01", "사회"),
    ("환경을 위한 작은 실천", "이초록", "가상지식사", "2023-04-22", "환경"),
    ("운영체제 원리 실습", "박시스템", "가상공학사", "2024-07-01", "컴퓨터"),
    ("졸업 프로젝트 기록법", "최프로젝트", "가상교육출판", "2025-02-01", "컴퓨터"),
]


def seed_database(config=None):
    """빈 프로젝트 DB에만 삽입한다. 재실행으로 사용자 데이터를 덮어쓰지 않는다."""
    cfg = current_app.config if config is None else config
    base_value = cfg.get("REFERENCE_DATE")
    base = date.fromisoformat(base_value) if isinstance(base_value, str) and base_value else (base_value or datetime.now(timezone(timedelta(hours=9))).date())
    admin_password = cfg.get("DEMO_ADMIN_PASSWORD", "admin123!")
    member_password = cfg.get("DEMO_MEMBER_PASSWORD", "member123!")
    with transaction(cfg) as conn:
        with conn.cursor() as cur:
            for table in ("book", "member", "owned_book", "user_account", "loan"):
                cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
                if cur.fetchone()["n"]:
                    raise ValueError("샘플 데이터는 5개 테이블이 모두 비어 있는 새 DB에만 넣을 수 있습니다.")
            books = []
            for title, author, publisher, published, category in BOOKS:
                cur.execute("""INSERT INTO book
                    (isbn,title,author,publisher,pub_date,category,metadata_source)
                    VALUES (NULL,%s,%s,%s,%s,%s,'SAMPLE')""", (title, author, publisher, published, category))
                books.append(cur.lastrowid)
            members = []
            for i in range(1, 9):
                status = "SUSPENDED" if i == 6 else "WITHDRAWN" if i == 7 else "ACTIVE"
                cur.execute("INSERT INTO member (name,phone,join_date,status) VALUES (%s,%s,%s,%s)",
                            (f"테스트회원{i:02d}", f"000-0000-{i:04d}", base-timedelta(days=120+i), status))
                members.append(cur.lastrowid)
            # 도서 1~4는 2권씩, 5~17은 1권씩, 도서 18은 아직 소장본 없음.
            copies = []
            serial = 1
            for i, book_id in enumerate(books[:17]):
                for _ in range(2 if i < 4 else 1):
                    owned_id = f"B{serial:03d}"
                    state = "LOST" if serial == 20 else "DISCARDED" if serial == 21 else "NORMAL"
                    cur.execute("INSERT INTO owned_book (owned_id,book_id,location,status) VALUES (%s,%s,%s,%s)",
                                (owned_id, book_id, f"실습자료실 {1+i//6}구역", state))
                    copies.append(owned_id)
                    serial += 1
            # 정상 대출, 현재 연체, 예정일 당일, 정상 반납, 과거 지연 반납을 구분한다.
            loans = [
                (members[0], "B001", -3, 11, None),
                (members[1], "B003", -20, -6, None),
                (members[2], "B005", -14, 0, None),
                (members[3], "B007", -2, 12, None),
                (members[3], "B009", -2, 12, None),
                (members[3], "B010", -2, 12, None),
                (members[0], "B002", -50, -36, -40),
                (members[0], "B003", -45, -31, -25),
                (members[1], "B001", -65, -51, -52),
                (members[2], "B004", -40, -26, -28),
                (members[4], "B006", -30, -16, -18),
                (members[4], "B008", -25, -11, -8),
                (members[5], "B011", -60, -46, -50),
                (members[6], "B012", -55, -41, -43),
            ]
            for mid, oid, start, due, returned in loans:
                cur.execute("INSERT INTO loan (member_id,owned_id,loan_date,due_date,return_date) VALUES (%s,%s,%s,%s,%s)",
                            (mid, oid, base+timedelta(days=start), base+timedelta(days=due),
                             None if returned is None else base+timedelta(days=returned)))
            cur.execute("INSERT INTO user_account (login_id,password_hash,role,member_id) VALUES (%s,%s,'ADMIN',NULL)",
                        ("admin", generate_password_hash(admin_password)))
            for i, mid in enumerate(members, 1):
                if i == 7:  # 탈퇴 회원은 계정 발급 대상에서 제외한다.
                    continue
                cur.execute("INSERT INTO user_account (login_id,password_hash,role,member_id) VALUES (%s,%s,'MEMBER',%s)",
                            (f"member{i:02d}", generate_password_hash(member_password), mid))
    return {
        "book": len(books), "owned_book": len(copies), "member": len(members),
        "loan": len(loans), "user_account": 8, "reference_date": base.isoformat(),
        "data_kind": "SYNTHETIC_COURSEWORK_DATA",
        "demo_accounts": [
            {"login_id": "admin", "password": admin_password, "role": "ADMIN"},
            {"login_id": "member01", "password": member_password, "role": "MEMBER"},
        ],
    }
