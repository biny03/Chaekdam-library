-- DBA 권한으로 실행하는 설정 예시. 실제 암호는 로컬에서 바꾼다.
-- 웹 앱의 DB_USER와 NL2SQL의 DB_READ_USER를 분리한다.
-- 아래 계정은 프로젝트 DB의 분석용 네 테이블에만 SELECT할 수 있다.
-- user_account에는 권한을 주지 않으므로 비밀번호 해시 조회를 차단한다.
-- member는 허용한 열에만 SELECT 권한을 주며 연락처(phone)는 제외한다.
-- 배포 전 CHANGE_ME 값을 변경하고 .env에 같은 암호를 넣는다.

CREATE USER IF NOT EXISTS 'library_reader'@'127.0.0.1'
IDENTIFIED BY 'CHANGE_ME_READER_PASSWORD';

GRANT SELECT ON library_termproject.book TO 'library_reader'@'127.0.0.1';
GRANT SELECT ON library_termproject.owned_book TO 'library_reader'@'127.0.0.1';
GRANT SELECT (member_id,name,join_date,status) ON library_termproject.member TO 'library_reader'@'127.0.0.1';
GRANT SELECT ON library_termproject.loan TO 'library_reader'@'127.0.0.1';

-- DB_NAME을 다르게 설정했다면 위 네 GRANT의 DB명도 맞춘다.
-- 기존 계정의 암호를 바꾸려면 DBA가 별도로 ALTER USER를 실행한다.
