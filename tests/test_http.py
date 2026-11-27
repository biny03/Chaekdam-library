"""Test the HTTP contract with an isolated MySQL; only the external response is mocked."""
import json
from io import BytesIO

from library_app import external_books


def login(client, username, password):
    token = client.get('/api/session').json['csrf_token']
    response = client.post('/api/login', json={'login_id':username, 'password':password},
                           headers={'X-CSRF-Token':token})
    assert response.status_code == 200, response.json
    return {'X-CSRF-Token':response.json['csrf_token']}


def test_member_can_read_only_own_loans_and_cannot_write(app):
    client = app.test_client()
    assert client.get('/api/books').status_code == 401
    headers = login(client, 'member01', 'member123!')
    assert client.get('/api/books').status_code == 200
    rows = client.get('/api/loans?member_id=2').json['items']
    assert rows and all(row['member_id'] == 1 for row in rows)
    assert client.post('/api/books', json={'title':'권한 검증'}, headers=headers).status_code == 403
    assert client.post('/api/nl2sql', json={'question':'책 수'}, headers=headers).status_code == 403
    assert client.get('/api/members').status_code == 403


def test_checkout_return_and_csrf(app):
    client = app.test_client()
    headers = login(client, 'admin', 'admin123!')
    req = {'member_id':8, 'owned_id':'B002'}
    assert client.post('/api/loans', json=req).status_code == 403
    response = client.post('/api/loans', json=req, headers=headers)
    assert response.status_code == 201, response.json
    assert response.json['due_date'] == '2026-12-07'
    assert client.post('/api/loans', json=req, headers=headers).status_code == 409
    loan_id = response.json['loan_id']
    assert client.post(f'/api/loans/{loan_id}/return', json={}, headers=headers).json['return_date'] == '2026-11-23'
    assert client.post(f'/api/loans/{loan_id}/return', json={}, headers=headers).status_code == 409


def test_import_metadata_preserves_optional_fields(app, monkeypatch):
    app.config['NL_API_KEY'] = 'stub-key'
    record = {'EA_ISBN':'9788937460449', 'TITLE':'서지 응답 예제', 'AUTHOR':'예제 저자',
              'PUBLISHER':'예제 출판처', 'PUBLISH_PREDATE':'20250225'}
    monkeypatch.setattr(external_books, 'urlopen', lambda req, timeout:BytesIO(json.dumps({'docs':[record]}).encode()))
    client = app.test_client()
    headers = login(client, 'admin', 'admin123!')
    items = client.get('/api/external/books?q=9788937460449&by=isbn').json['items']
    data = items[0]
    assert data['planned_pub_date'] == '20250225' and data['pub_date'] is None
    created = client.post('/api/external/import', json=data, headers=headers)
    assert created.status_code == 201, created.json
    assert created.json['metadata_source'] == 'NL_ISBN' and created.json['imported_at']
    assert created.json['pub_date'] is None
    assert client.post('/api/external/import', json=data, headers=headers).status_code == 409


def test_missing_keys_do_not_produce_results(app):
    client = app.test_client()
    headers = login(client, 'admin', 'admin123!')
    assert client.get('/api/external/books?q=예제&by=title').status_code == 503
    response = client.post('/api/nl2sql', json={'question':'책 수','prompt_mode':'simple'}, headers=headers)
    assert response.status_code == 400 and response.json['error']['code'] == 'missing_api_key'


def test_public_signup_is_atomic_and_creates_only_own_member(app):
    client = app.test_client()
    headers = {'X-CSRF-Token': client.get('/api/session').json['csrf_token']}
    data = dict(name='가입테스트', phone='010-0000-0000', login_id='new-reader',
                password='reader-test123!', role='ADMIN', member_id=1)
    assert client.post('/api/register', json=data).status_code == 403
    assert client.post('/api/register', json=data, headers=headers).status_code == 201
    assert client.post('/api/register', json=data, headers=headers).status_code == 409
    login(client, data['login_id'], data['password'])
    user = client.get('/api/session').json['user']
    assert user['role'] == 'MEMBER' and user['member_id'] != 1
    assert client.get('/api/loans').json['items'] == []
    assert client.get('/api/members').status_code == 403
    staff = app.test_client()
    login(staff, 'admin', 'admin123!')
    members = staff.get('/api/members', query_string={'q':'가입테스트'}).json['items']
    assert len(members) == 1 and members[0]['member_id'] == user['member_id']


def test_member_requests_copy_and_staff_approves_once(app):
    from library_app import services
    member, staff = app.test_client(), app.test_client()
    token = member.get('/api/session').json['csrf_token']
    data = dict(name='신청테스트',phone='010-0000-0000',login_id='request-reader',password='reader-test123!')
    assert member.post('/api/register',json=data,headers={'X-CSRF-Token':token}).status_code == 201
    mh = login(member,data['login_id'],data['password'])
    ah = login(staff,'admin','admin123!')
    submitted = member.post('/api/loan-requests',json={'member_id':1,'owned_id':'B002'},headers=mh)
    assert submitted.status_code == 201
    rid = submitted.json['request_id']
    assert member.post('/api/loan-requests',json={'owned_id':'B002'},headers=mh).status_code == 409
    assert member.get('/api/loans').json['items'] == []
    assert member.get('/api/loan-requests').json['items'][0]['member_id'] == member.get('/api/session').json['user']['member_id']
    assert member.post(f'/api/loan-requests/{rid}/approve',json={},headers=mh).status_code == 403
    assert staff.post(f'/api/loan-requests/{rid}/approve',json={},headers=ah).status_code == 200
    assert member.get('/api/loans').json['items'][0]['owned_id'] == 'B002'
    assert member.get('/api/loan-requests').json['items'][0]['status'] == 'APPROVED'
    assert staff.post(f'/api/loan-requests/{rid}/approve',json={},headers=ah).status_code == 409


def test_catalog_search_with_repeated_category_and_availability(app, monkeypatch):
    from library_app import catalog_search
    client = app.test_client()
    headers = login(client, 'member01', 'member123!')
    filters = dict(field='title', term='수학책', category='수학', available_only=True)
    reply = json.dumps({'done':True,'response':json.dumps(filters)})
    monkeypatch.setattr(catalog_search, 'urlopen', lambda req, timeout: BytesIO(reply.encode()))
    res = client.post('/api/books/search',json={'q':'대출 가능한 수학책'},headers=headers)
    assert res.status_code == 200 and res.json['items']
    assert all(b['category']=='수학' and b['available_count']>0 for b in res.json['items'])
