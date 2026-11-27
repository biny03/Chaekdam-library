"""Create the project database and grant separate application/analysis permissions."""
from argparse import ArgumentParser
from getpass import getpass
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql
from library_app.config import settings
from library_app.seed import seed_database


def identifier(value, field):
    if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,62}', value):
        raise ValueError(f'{field}에는 영문으로 시작하는 영문, 숫자, 밑줄을 사용하세요.')
    return f'`{value}`'


def initialize(cfg, admin_user, admin_password, seed=False):
    database = identifier(cfg['DB_NAME'], 'DB_NAME')
    app_user, reader = cfg['DB_USER'], cfg['DB_READ_USER']
    if app_user == reader or {app_user.lower(), reader.lower()} & {admin_user.lower(), 'root'}:
        raise ValueError('관리용, 웹 앱용, NL2SQL 조회용 MySQL 계정은 각각 달라야 합니다.')
    for name in (app_user, reader):
        identifier(name, 'MySQL 사용자명')
    if not cfg['DB_PASSWORD'] or not cfg['DB_READ_PASSWORD']:
        raise ValueError('.env에 앱 및 조회 계정 비밀번호를 각각 설정하세요.')
    conn = pymysql.connect(host=cfg['DB_HOST'], port=cfg['DB_PORT'], user=admin_user,
                           password=admin_password, charset='utf8mb4', autocommit=True,
                           connect_timeout=5)
    # MySQL sees locally launched TCP clients from 127.0.0.1. A remote server's
    # account host must be provisioned by its DBA, so keep this bootstrap local.
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE IF NOT EXISTS {database} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
            cur.execute(f'USE {database}')
            commands = (ROOT / 'sql/schema.sql').read_text(encoding='utf-8')
            for command in commands.split(';'):
                if command.strip():
                    cur.execute(command)
            for user, password in ((app_user, cfg['DB_PASSWORD']), (reader, cfg['DB_READ_PASSWORD'])):
                cur.execute("CREATE USER IF NOT EXISTS %s@'127.0.0.1' IDENTIFIED BY %s", (user, password))
            cur.execute(f"GRANT SELECT, INSERT, UPDATE ON {database}.* TO %s@'127.0.0.1'", (app_user,))
            for table in ('book', 'owned_book', 'loan'):
                cur.execute(f"GRANT SELECT ON {database}.`{table}` TO %s@'127.0.0.1'", (reader,))
            cur.execute(f"GRANT SELECT (member_id, name, join_date, status) ON {database}.member TO %s@'127.0.0.1'", (reader,))
    finally:
        conn.close()
    return seed_database(cfg) if seed else None


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--seed', action='store_true', help='비어 있는 DB에 과제용 샘플 데이터 설치')
    parser.add_argument('--admin-user', default='root')
    args = parser.parse_args()
    password = os.environ.get('LIBRARY_DB_ADMIN_PASSWORD')
    if password is None:
        password = getpass('MySQL 관리자 비밀번호: ')
    try:
        cfg = settings()
        result = initialize(cfg, args.admin_user, password, args.seed)
    except (pymysql.MySQLError, ValueError, RuntimeError) as error:
        message = error.args[-1] if error.args else type(error).__name__
        print(f'초기화 실패: {message}', file=sys.stderr)
        return 1
    print(f"초기화 완료: {cfg['DB_NAME']}")
    if result:
        print('샘플:', ', '.join(f'{table}={result[table]}' for table in ('book','owned_book','member','loan','user_account')))
        print(f"샘플 기준일: {result['reference_date']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
