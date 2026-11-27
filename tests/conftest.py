import os
import uuid
import pytest
import pymysql

from library_app import create_app
from scripts.init_db import initialize


@pytest.fixture
def app():
    port = os.getenv('LIBRARY_TEST_MYSQL_PORT')
    if not port:
        pytest.skip('격리 MySQL의 LIBRARY_TEST_MYSQL_PORT를 설정하세요.')
    port = int(port)
    if port == 3306:
        pytest.fail('테스트는 기본 서비스와 분리한 MySQL 포트에서 실행하세요.')
    admin_password = os.getenv('LIBRARY_TEST_MYSQL_PASSWORD', '')
    suffix = uuid.uuid4().hex[:10]
    cfg = dict(TESTING=True, SECRET_KEY='isolated-test-key', DB_HOST='127.0.0.1',
               DB_PORT=port, DB_NAME=f'library_test_{suffix}', DB_USER=f'app_{suffix}',
               DB_PASSWORD=f'A!{suffix}Z8', DB_READ_USER=f'reader_{suffix}',
               DB_READ_PASSWORD=f'R!{suffix}Z8', REFERENCE_DATE='2026-11-23',
               LLM_PROVIDER='openai', OPENAI_API_KEY='', NL_API_KEY='', MAX_LOANS=3, LOAN_DAYS=14)
    try:
        initialize(cfg, 'root', admin_password, seed=True)
        yield create_app(cfg)
    finally:
        conn = pymysql.connect(host='127.0.0.1', port=port, user='root', password=admin_password, autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{cfg['DB_NAME']}`")
                for user in (cfg['DB_USER'], cfg['DB_READ_USER']):
                    cur.execute("DROP USER IF EXISTS %s@'127.0.0.1'", (user,))
        finally:
            conn.close()
