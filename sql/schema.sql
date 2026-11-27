-- 6조 도서 관리 프로그램 / MySQL 8.0.16 이상
-- 원본 중간 설계의 5개 테이블을 유지한다.
-- DB 선택은 초기화 프로그램에서 수행한다. 기존 데이터 삭제 문장은 없다.

CREATE TABLE IF NOT EXISTS book (
    book_id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    isbn CHAR(13) NULL,
    title VARCHAR(200) NOT NULL,
    author VARCHAR(100) NULL,
    publisher VARCHAR(100) NULL,
    pub_date DATE NULL,
    category VARCHAR(50) NULL,
    metadata_source VARCHAR(30) NOT NULL DEFAULT 'MANUAL',
    source_url VARCHAR(500) NULL,
    imported_at DATETIME NULL,
    PRIMARY KEY (book_id),
    UNIQUE KEY uq_book_isbn (isbn),
    CONSTRAINT ck_book_title CHECK (CHAR_LENGTH(TRIM(title)) > 0),
    CONSTRAINT ck_book_isbn CHECK (isbn IS NULL OR isbn REGEXP '^[0-9]{13}$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS member (
    member_id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    name VARCHAR(50) NOT NULL,
    phone VARCHAR(20) NOT NULL,
    join_date DATE NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'ACTIVE',
    PRIMARY KEY (member_id),
    CONSTRAINT ck_member_name CHECK (CHAR_LENGTH(TRIM(name)) > 0),
    CONSTRAINT ck_member_phone CHECK (CHAR_LENGTH(TRIM(phone)) > 0),
    CONSTRAINT ck_member_status CHECK (status IN ('ACTIVE','SUSPENDED','WITHDRAWN'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS owned_book (
    owned_id VARCHAR(20) NOT NULL,
    book_id INT UNSIGNED NOT NULL,
    location VARCHAR(50) NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'NORMAL',
    PRIMARY KEY (owned_id),
    KEY ix_owned_book_book (book_id),
    CONSTRAINT fk_owned_book_book FOREIGN KEY (book_id) REFERENCES book(book_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_owned_id CHECK (CHAR_LENGTH(TRIM(owned_id)) > 0),
    CONSTRAINT ck_owned_location CHECK (CHAR_LENGTH(TRIM(location)) > 0),
    CONSTRAINT ck_owned_status CHECK (status IN ('NORMAL','LOST','DISCARDED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS user_account (
    account_id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    login_id VARCHAR(30) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(10) NOT NULL,
    member_id INT UNSIGNED NULL,
    PRIMARY KEY (account_id),
    UNIQUE KEY uq_account_login (login_id),
    UNIQUE KEY uq_account_member (member_id),
    CONSTRAINT fk_account_member FOREIGN KEY (member_id) REFERENCES member(member_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_account_login CHECK (CHAR_LENGTH(TRIM(login_id)) > 0),
    CONSTRAINT ck_account_role CHECK (
        (role = 'ADMIN' AND member_id IS NULL)
        OR (role = 'MEMBER' AND member_id IS NOT NULL)
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS loan (
    loan_id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    member_id INT UNSIGNED NOT NULL,
    owned_id VARCHAR(20) NOT NULL,
    loan_date DATE NOT NULL,
    due_date DATE NOT NULL,
    return_date DATE NULL,
    open_flag TINYINT GENERATED ALWAYS AS (
        CASE WHEN return_date IS NULL THEN 1 ELSE NULL END
    ) STORED,
    PRIMARY KEY (loan_id),
    UNIQUE KEY uq_loan_owned_open (owned_id, open_flag),
    KEY ix_loan_member_return (member_id, return_date),
    KEY ix_loan_return_due (return_date, due_date),
    CONSTRAINT fk_loan_member FOREIGN KEY (member_id) REFERENCES member(member_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_loan_owned FOREIGN KEY (owned_id) REFERENCES owned_book(owned_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_loan_due_date CHECK (due_date >= loan_date),
    CONSTRAINT ck_loan_return_date CHECK (return_date IS NULL OR return_date >= loan_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS loan_request (
    request_id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    member_id INT UNSIGNED NOT NULL,
    owned_id VARCHAR(20) NOT NULL,
    status VARCHAR(12) NOT NULL DEFAULT 'PENDING',
    requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    pending_flag TINYINT GENERATED ALWAYS AS (CASE WHEN status='PENDING' THEN 1 ELSE NULL END) STORED,
    UNIQUE KEY uq_pending_request (member_id,owned_id,pending_flag),
    KEY ix_request_copy_status (owned_id,status),
    FOREIGN KEY (member_id) REFERENCES member(member_id),
    FOREIGN KEY (owned_id) REFERENCES owned_book(owned_id),
    CHECK (status IN ('PENDING','APPROVED','DECLINED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
