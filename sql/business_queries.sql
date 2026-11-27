-- 요구사항별 SQL 예제 / MySQL 8
-- SELECT는 샘플 DB에서 실행 가능하다. 변경 예제는 주석 처리했다.
-- 실제 사용자 입력은 services.py에서 %s 바인딩으로 전달한다.

-- Q01 도서 검색과 대출 가능 수량 / F03
-- 정상 소장본 중 미반납 대출이 없는 책만 대출 가능으로 센다.
SELECT b.book_id,b.title,b.author,b.publisher,b.isbn,
       COUNT(o.owned_id) AS total_count,
       COALESCE(SUM(o.status='NORMAL'),0) AS normal_count,
       COALESCE(SUM(o.status='NORMAL' AND l.loan_id IS NULL),0) AS available_count
FROM book b
LEFT JOIN owned_book o ON o.book_id=b.book_id
LEFT JOIN loan l ON l.owned_id=o.owned_id AND l.return_date IS NULL
WHERE b.title LIKE '%데이터베이스%'
GROUP BY b.book_id,b.title,b.author,b.publisher,b.isbn
ORDER BY b.book_id;

-- Q02 회원의 대출 현황 / F04, F09
SELECT m.member_id,m.name,m.status,COUNT(l.loan_id) AS active_loans
FROM member m LEFT JOIN loan l ON l.member_id=m.member_id AND l.return_date IS NULL
GROUP BY m.member_id,m.name,m.status ORDER BY m.member_id;

-- Q03 현재 연체 / F10
-- 과거 지연 반납은 제외하고 예정일 당일도 연체로 계산하지 않는다.
SELECT l.loan_id,m.name,b.title,l.owned_id,l.due_date,
       DATEDIFF(CURRENT_DATE,l.due_date) AS overdue_days
FROM loan l JOIN member m ON m.member_id=l.member_id
JOIN owned_book o ON o.owned_id=l.owned_id JOIN book b ON b.book_id=o.book_id
WHERE l.return_date IS NULL AND l.due_date < CURRENT_DATE
ORDER BY l.due_date,l.loan_id;

-- Q04 회원별 전체 대출 이력 / F09
SELECT l.loan_id,l.member_id,b.title,l.loan_date,l.due_date,l.return_date
FROM loan l JOIN owned_book o ON o.owned_id=l.owned_id
JOIN book b ON b.book_id=o.book_id
WHERE l.member_id=1 ORDER BY l.loan_id DESC;

-- Q05 대출 가능 책 / F02, F03
SELECT o.owned_id,b.title,o.location
FROM owned_book o JOIN book b ON b.book_id=o.book_id
WHERE o.status='NORMAL' AND NOT EXISTS (
    SELECT 1 FROM loan l WHERE l.owned_id=o.owned_id AND l.return_date IS NULL
) ORDER BY o.owned_id;

-- Q06 분류별 소장 수 / 집계
SELECT b.category,COUNT(DISTINCT b.book_id) AS titles,COUNT(o.owned_id) AS normal_copies
FROM book b LEFT JOIN owned_book o ON o.book_id=b.book_id AND o.status='NORMAL'
GROUP BY b.category ORDER BY b.category;

-- Q07 도서 등록 / F01, F12 외부 API 입력
-- INSERT INTO book (isbn,title,author,publisher,pub_date,category,metadata_source)
-- VALUES (NULL,'실습용 새 도서','가상저자','가상출판사',NULL,'컴퓨터','MANUAL');

-- Q08 소장 도서 등록 / F02
-- INSERT INTO owned_book (owned_id,book_id,location,status)
-- VALUES ('NEW001',1,'실습자료실 1구역','NORMAL');

-- Q09 회원 등록/수정/탈퇴 / F04, F05
-- INSERT INTO member (name,phone,join_date,status)
-- VALUES ('테스트회원','000-0000-0000',CURRENT_DATE,'ACTIVE');
-- UPDATE member SET name='수정이름',phone='000-0000-0001' WHERE member_id=1;
-- 탈퇴는 member row FOR UPDATE 후 미반납 대출 존재 여부 검사와 같은 트랜잭션에서 실행.
-- UPDATE member SET status='WITHDRAWN' WHERE member_id=1;

-- Q10 대출 / F06, F07
-- START TRANSACTION;
-- SELECT status FROM member WHERE member_id=1 FOR UPDATE;
-- SELECT COUNT(*),SUM(due_date<CURRENT_DATE) FROM loan
-- WHERE member_id=1 AND return_date IS NULL;
-- SELECT status FROM owned_book WHERE owned_id='B002' FOR UPDATE;
-- SELECT loan_id FROM loan WHERE owned_id='B002' AND return_date IS NULL FOR UPDATE;
-- 모든 조건을 통과한 경우만 실행한다. 기간/한도는 환경 설정을 사용한다.
-- INSERT INTO loan (member_id,owned_id,loan_date,due_date)
-- VALUES (1,'B002',CURRENT_DATE,DATE_ADD(CURRENT_DATE,INTERVAL 14 DAY));
-- COMMIT;

-- Q11 반납 / F08
-- 회원 -> 소장 도서 -> 대출 행 순으로 잠그고, 미반납 여부를 확인한 뒤 갱신한다.
-- UPDATE loan SET return_date=CURRENT_DATE WHERE loan_id=1 AND return_date IS NULL;

-- Q12 로그인 / F11
-- SELECT account_id,login_id,password_hash,role,member_id FROM user_account WHERE login_id=%s;
-- 비밀번호는 Python의 check_password_hash로 검증하며 결과에 해시를 포함하지 않는다.

-- Q13 소장 상태 변경 / F02
-- 미반납 대출이 없는지 확인 후 분실/폐기 처리한다.
-- UPDATE owned_book SET status='LOST' WHERE owned_id='B020';

-- Q14 도서 정보 수정 / F01
-- UPDATE book SET title=%s,author=%s,publisher=%s,pub_date=%s,category=%s WHERE book_id=%s;

-- Q15 회원 계정 발급 / F11
-- INSERT INTO user_account(login_id,password_hash,role,member_id) VALUES (%s,%s,'MEMBER',%s);
