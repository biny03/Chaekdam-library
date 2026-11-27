"""MySQL connections shared by the web application and command-line tools."""
from contextlib import contextmanager

import pymysql
from flask import current_app


def connect(config=None, read_only=False):
    """Open one connection. Callers must close it; writes need an explicit commit.

    NL2SQL uses a separate SELECT-only MySQL account. Missing reader credentials
    never fall back to the application's writable account.
    """
    cfg = current_app.config if config is None else config
    user_key = "DB_READ_USER" if read_only else "DB_USER"
    password_key = "DB_READ_PASSWORD" if read_only else "DB_PASSWORD"
    user = cfg.get(user_key)
    if not user:
        raise ValueError(f"{user_key} 설정이 필요합니다.")
    return pymysql.connect(
        host=cfg.get("DB_HOST", "127.0.0.1"),
        port=int(cfg.get("DB_PORT", 3306)),
        user=user,
        password=cfg.get(password_key, ""),
        database=cfg.get("DB_NAME", "library_termproject"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=5,
        read_timeout=int(cfg.get("DB_READ_TIMEOUT", 10)),
        write_timeout=10,
        init_command="SET time_zone = '+09:00'",
    )


@contextmanager
def transaction(config=None):
    conn = connect(config)
    try:
        conn.begin()
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
