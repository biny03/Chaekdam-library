import os
import secrets
from datetime import timedelta
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

def settings():
    load_dotenv(ROOT / '.env', override=False)
    result = {
        'SECRET_KEY': os.getenv('SECRET_KEY') or secrets.token_hex(32),
        'DB_HOST': os.getenv('DB_HOST', '127.0.0.1'),
        'DB_PORT': int(os.getenv('DB_PORT', '3306')),
        'DB_NAME': os.getenv('DB_NAME', 'library_termproject'),
        'DB_USER': os.getenv('DB_USER', 'library_app'),
        'DB_PASSWORD': os.getenv('DB_PASSWORD', ''),
        'DB_READ_USER': os.getenv('DB_READ_USER', 'library_reader'),
        'DB_READ_PASSWORD': os.getenv('DB_READ_PASSWORD', ''),
        'APP_PORT': int(os.getenv('APP_PORT', '5000')),
        'MAX_LOANS': int(os.getenv('MAX_LOANS', '3')),
        'LOAN_DAYS': int(os.getenv('LOAN_DAYS', '14')),
        'REFERENCE_DATE': os.getenv('REFERENCE_DATE') or None,
        'LLM_PROVIDER': os.getenv('LLM_PROVIDER', 'ollama').strip().lower(),
        'OLLAMA_MODEL': os.getenv('OLLAMA_MODEL', 'qwen2.5-coder:3b'),
        'OPENAI_API_KEY': os.getenv('OPENAI_API_KEY', ''),
        'OPENAI_MODEL': os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
        'NL_API_KEY': os.getenv('NL_API_KEY', ''),
        'SESSION_COOKIE_HTTPONLY': True,
        'SESSION_COOKIE_SAMESITE': 'Strict',
        'PERMANENT_SESSION_LIFETIME': timedelta(hours=2),
        'MAX_CONTENT_LENGTH': 64 * 1024,
        'JSON_AS_ASCII': False,
    }
    if result['LLM_PROVIDER'] not in {'ollama', 'openai'}:
        raise ValueError('LLM_PROVIDER에는 ollama 또는 openai를 입력하세요.')
    if not 1 <= result['MAX_LOANS'] <= 100 or not 1 <= result['LOAN_DAYS'] <= 365:
        raise ValueError('MAX_LOANS와 LOAN_DAYS 설정 범위를 확인하세요.')
    return result
