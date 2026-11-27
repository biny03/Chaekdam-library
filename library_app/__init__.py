"""HTTP endpoints: authentication -> role checks -> parameterized domain SQL."""
from datetime import date, datetime
from decimal import Decimal
from functools import wraps
import secrets
import time

from flask import Flask, jsonify, request, session, g, render_template
from flask.json.provider import DefaultJSONProvider
from werkzeug.security import check_password_hash
from werkzeug.exceptions import HTTPException
import pymysql
from .config import settings
from .db import connect
from . import services
from .external_books import search_books, BookAPIError

class JSONProvider(DefaultJSONProvider):
    ensure_ascii = False

    @staticmethod
    def default(value):
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        return DefaultJSONProvider.default(value)

def create_app(overrides=None):
    app = Flask(__name__)
    app.json = JSONProvider(app)
    app.config.update(settings())
    app.config.update(overrides or {})
    login_attempts = {}

    def fail(message, code='BAD_REQUEST', status=400):
        return jsonify(error={'message': message, 'code': code}), status

    def payload():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise services.DomainError('JSON 객체를 보내 주세요.')
        return data

    def auth(admin=False):
        def decorate(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                if not g.user:
                    return fail('로그인 후 이용해 주세요.', 'AUTH_REQUIRED', 401)
                if admin and g.user['role'] != 'ADMIN':
                    return fail('관리자만 사용할 수 있습니다.', 'FORBIDDEN', 403)
                return fn(*args, **kwargs)
            return wrapped
        return decorate

    @app.before_request
    def load_user_and_csrf():
        if not request.path.startswith('/api/'):
            return None
        session.setdefault('csrf_token', secrets.token_urlsafe(32))
        if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            token = request.headers.get('X-CSRF-Token', '')
            if not secrets.compare_digest(token, session['csrf_token']):
                return fail('화면을 새로고침한 뒤 다시 시도해 주세요.', 'CSRF_FAILED', 403)
        g.user = None
        if session.get('account_id'):
            conn = connect()
            try:
                with conn.cursor() as cur:
                    cur.execute('SELECT a.account_id,a.login_id,a.role,a.member_id,m.name,m.status FROM user_account a LEFT JOIN member m ON a.member_id=m.member_id WHERE a.account_id=%s', (session['account_id'],))
                    row = cur.fetchone()
                if row and (row['role'] == 'ADMIN' or row['status'] != 'WITHDRAWN'):
                    g.user = row
                else:
                    session.pop('account_id', None)
            finally:
                conn.close()

    @app.after_request
    def response_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(services.DomainError)
    def domain_error(error):
        return fail(str(error), getattr(error, 'code', 'DOMAIN_ERROR'), getattr(error, 'status_code', 400))

    @app.errorhandler(BookAPIError)
    def book_error(error):
        return fail(str(error), error.code, 503 if error.code == 'MISSING_API_KEY' else 400)

    @app.errorhandler(pymysql.MySQLError)
    def database_error(error):
        if isinstance(error, pymysql.IntegrityError):
            return fail('중복 값 또는 데이터 연결 조건을 확인해 주세요.', 'CONSTRAINT_ERROR', 409)
        return fail('데이터베이스 연결 또는 SQL 처리에 실패했습니다. 설정과 서버 상태를 확인해 주세요.', 'DATABASE_ERROR', 503)

    @app.errorhandler(HTTPException)
    def http_error(error):
        return fail('요청 주소, 방식 또는 데이터 크기를 확인해 주세요.', 'HTTP_ERROR', error.code or 400)

    @app.get('/')
    def index():
        return render_template('index.html')

    def session_info():
        return {'user': g.user, 'csrf_token': session['csrf_token'],
                'settings': {'max_loans': app.config['MAX_LOANS'], 'loan_days': app.config['LOAN_DAYS'],
                             'reference_date': services.today().isoformat()},
                'integrations': {'provider': app.config['LLM_PROVIDER'], 'ollama_model': app.config['OLLAMA_MODEL'], 'openai': bool(app.config['OPENAI_API_KEY']), 'nl_books': bool(app.config['NL_API_KEY'])}}

    @app.get('/api/session')
    def get_session():
        return jsonify(session_info())

    @app.post('/api/register')
    def register():
        if g.user:
            return fail('로그아웃 후 가입해 주세요.', 'ALREADY_SIGNED_IN', 409)
        return jsonify(services.register_member(payload())), 201

    @app.post('/api/login')
    def login():
        data = payload()
        login_id, password = str(data.get('login_id', '')).strip(), str(data.get('password', ''))
        if not 1 <= len(login_id) <= 30 or not 1 <= len(password) <= 128:
            return fail('아이디와 비밀번호를 확인해 주세요.', 'LOGIN_FAILED', 401)
        attempt_key = (request.remote_addr, login_id)
        recent = [ts for ts in login_attempts.get(attempt_key, []) if ts > time.monotonic() - 60]
        if len(recent) >= 10:
            return fail('로그인 시도가 많습니다. 잠시 후 다시 시도해 주세요.', 'RATE_LIMITED', 429)
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT a.*,m.name,m.status FROM user_account a LEFT JOIN member m ON a.member_id=m.member_id WHERE login_id=%s', (login_id,))
                row = cur.fetchone()
        finally:
            conn.close()
        if not row or not check_password_hash(row['password_hash'], password) or (row['role'] == 'MEMBER' and row['status'] == 'WITHDRAWN'):
            login_attempts[attempt_key] = recent + [time.monotonic()]
            return fail('아이디와 비밀번호 또는 계정 상태를 확인해 주세요.', 'LOGIN_FAILED', 401)
        login_attempts.pop(attempt_key, None)
        session.clear()
        session.update(account_id=row['account_id'], csrf_token=secrets.token_urlsafe(32))
        session.permanent = True
        g.user = {key: row.get(key) for key in ('account_id', 'login_id', 'role', 'member_id', 'name', 'status')}
        return jsonify(session_info())

    @app.post('/api/logout')
    def logout():
        session.clear()
        session['csrf_token'] = secrets.token_urlsafe(32)
        g.user = None
        return jsonify(session_info())

    @app.get('/api/dashboard')
    @auth()
    def dashboard():
        return jsonify(services.dashboard(member_id=g.user['member_id'] if g.user['role'] == 'MEMBER' else None))

    @app.get('/api/books')
    @auth()
    def books():
        return jsonify(items=services.list_books(query=request.args.get('q', '')[:200], category=request.args.get('category', '')[:50]))

    @app.post('/api/books/search')
    @auth()
    def search_catalog():
        from .catalog_search import search
        data = payload()
        return jsonify(search(data.get('q'), data.get('category', ''), app.config))

    @app.get('/api/books/<int:book_id>')
    @auth()
    def book(book_id):
        result = services.book_detail(book_id, viewer_id=g.user['member_id'])
        if g.user['role'] == 'MEMBER':
            for copy in result.get('copies', []):
                for key in ('member_id', 'member_name', 'phone', 'loan_id', 'active_loan_id'):
                    copy.pop(key, None)
        return jsonify(result)

    @app.post('/api/books')
    @auth(admin=True)
    def add_book():
        return jsonify(services.save_book(book_fields(payload()))), 201

    @app.patch('/api/books/<int:book_id>')
    @auth(admin=True)
    def edit_book(book_id):
        return jsonify(services.save_book(book_fields(payload()), book_id=book_id))

    @app.post('/api/copies')
    @auth(admin=True)
    def add_copy():
        return jsonify(services.add_owned(payload())), 201

    @app.patch('/api/copies/<owned_id>')
    @auth(admin=True)
    def edit_copy(owned_id):
        return jsonify(services.update_owned(owned_id, payload()))

    @app.get('/api/members')
    @auth(admin=True)
    def members():
        return jsonify(items=services.list_members(query=request.args.get('q', '')[:100]))

    @app.post('/api/members')
    @auth(admin=True)
    def add_member():
        return jsonify(services.save_member(payload())), 201

    @app.patch('/api/members/<int:member_id>')
    @auth(admin=True)
    def edit_member(member_id):
        return jsonify(services.save_member(payload(), member_id=member_id))

    @app.post('/api/accounts')
    @auth(admin=True)
    def add_account():
        return jsonify(services.create_account(payload())), 201

    @app.get('/api/loans')
    @auth()
    def loans():
        member_id = g.user['member_id'] if g.user['role'] == 'MEMBER' else request.args.get('member_id', type=int)
        return jsonify(items=services.list_loans(member_id=member_id, filter=request.args.get('filter', 'all')))

    @app.get('/api/loan-requests')
    @auth()
    def loan_requests():
        return jsonify(items=services.list_requests(None if g.user['role']=='ADMIN' else g.user['member_id']))

    @app.post('/api/loan-requests')
    @auth()
    def submit_request():
        if g.user['role'] != 'MEMBER':
            return fail('회원 계정에서 대출을 신청해 주세요.', 'MEMBER_ONLY', 403)
        return jsonify(services.request_loan(g.user['member_id'], payload().get('owned_id'))), 201

    @app.post('/api/loan-requests/<int:request_id>/<decision>')
    @auth(admin=True)
    def process_request(request_id, decision):
        return jsonify(services.decide_request(request_id, decision))

    @app.post('/api/loans')
    @auth(admin=True)
    def checkout():
        data = payload()
        return jsonify(services.checkout(data.get('member_id'), data.get('owned_id'))), 201

    @app.post('/api/loans/<int:loan_id>/return')
    @auth(admin=True)
    def return_book(loan_id):
        return jsonify(services.return_loan(loan_id))

    @app.get('/api/external/books')
    @auth(admin=True)
    def external_search():
        return jsonify(search_books(request.args.get('q'), request.args.get('by', 'isbn'), app.config))

    def book_fields(data):
        return {field: data[field] for field in ('isbn', 'title', 'author', 'publisher', 'pub_date', 'category') if field in data}

    @app.post('/api/external/import')
    @auth(admin=True)
    def external_import():
        if not app.config['NL_API_KEY']:
            raise BookAPIError('국립중앙도서관 인증키를 먼저 설정해 주세요.', 'MISSING_API_KEY')
        data = book_fields(payload())
        data['metadata_source'] = 'NL_ISBN'
        data['source_url'] = 'https://www.nl.go.kr/NL/contents/N31101030500.do'
        return jsonify(services.save_book(data)), 201

    @app.post('/api/nl2sql')
    @auth(admin=True)
    def nl2sql_query():
        from .nl2sql import run_question, NL2SQLError
        data = payload()
        try:
            return jsonify(run_question(data.get('question', ''), data.get('prompt_mode', 'enhanced'), app.config))
        except NL2SQLError as error:
            return fail(str(error), getattr(error, 'code', 'NL2SQL_ERROR'), 400)

    return app
