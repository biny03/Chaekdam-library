'use strict';

// 모든 API 응답은 textContent로 표시한다. 서버가 보낸 HTML은 실행하지 않는다.
const state = { session: null, page: 'dashboard', loanFilter: 'all', sql: '', loadId: 0 };
const $ = (id) => document.getElementById(id);
const admin = () => state.session?.user?.role === 'ADMIN';
const labels = { ACTIVE: '정상', SUSPENDED: '이용 정지', WITHDRAWN: '탈퇴', NORMAL: '정상', LOST: '분실', DISCARDED: '폐기', ADMIN: '관리자', MEMBER: '회원' };
const pages = ['dashboard', 'books', 'members', 'loans', 'external', 'nl2sql'];
let toastTimer;

function el(tag, text, attrs = {}) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'className') node.className = value;
    else node.setAttribute(key, String(value));
  }
  return node;
}
function text(value, fallback = '—') { return value === null || value === undefined || value === '' ? fallback : String(value); }
function dateInput(value) {
  if (!value) return '';
  if (/^\d{4}-\d{2}-\d{2}/.test(String(value))) return String(value).slice(0, 10);
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return '';
  return `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, '0')}-${String(parsed.getDate()).padStart(2, '0')}`;
}
function displayDate(value) { return dateInput(value) || text(value); }
function items(data) { return Array.isArray(data) ? data : (data?.items || []); }
function showToast(message, error = false) {
  clearTimeout(toastTimer);
  const toast = $('announcement');
  toast.textContent = message;
  toast.classList.toggle('error', error);
  toast.hidden = false;
  toastTimer = setTimeout(() => { toast.hidden = true; }, error ? 7000 : 4000);
}
async function api(path, method = 'GET', body) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), ['/api/nl2sql', '/api/books/search'].includes(path) ? 195000 : 100000);
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['X-CSRF-Token'] = state.session?.csrf_token || '';
  let response;
  try {
    response = await fetch(path, { method, headers, credentials: 'same-origin', signal: controller.signal, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
  } catch (error) {
    throw new Error(error.name === 'AbortError' ? '요청 시간이 초과되었습니다. 서버 상태를 확인하고 다시 시도하세요.' : '서버에 연결하지 못했습니다. 실행 상태를 확인해 주세요.');
  } finally { clearTimeout(timeout); }
  let result;
  try { result = await response.json(); } catch { throw new Error(`서버 응답을 읽지 못했습니다. (HTTP ${response.status})`); }
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') { state.session = null; showLogin(); }
    throw new Error(result?.error?.message || result?.message || `요청에 실패했습니다. (HTTP ${response.status})`);
  }
  return result;
}
function badge(status, customLabel) {
  const variant = ['LOST', 'WITHDRAWN', 'OVERDUE'].includes(status) ? 'danger' : ['SUSPENDED'].includes(status) ? 'warning' : ['DISCARDED', 'RETURNED'].includes(status) ? 'neutral' : '';
  return el('span', customLabel || labels[status] || status, { className: `badge ${variant}` });
}
function button(label, action, className = 'button secondary small') {
  const node = el('button', label, { type: 'button', className });
  node.addEventListener('click', () => Promise.resolve(action()).catch((error) => showToast(error.message, true)));
  return node;
}
function actions(...buttons) { const node = el('div', null, { className: 'row-actions' }); node.append(...buttons); return node; }
function canBorrow(copy) {
  if (copy.available !== undefined && copy.available !== null) return copy.available === true || copy.available === 1;
  return copy.status === 'NORMAL' && !copy.active_loan_id && !copy.loan_id;
}
function empty(container, title = '표시할 내역이 없습니다.', description = '검색 조건을 바꾸거나 새 데이터를 등록해 주세요.') {
  const wrapper = el('div', null, { className: 'empty-state' });
  wrapper.append(el('span', '▤', { 'aria-hidden': 'true' }), el('h3', title), el('p', description));
  container.replaceChildren(wrapper);
}
function loading(container) { container.replaceChildren(el('div', '불러오는 중입니다…', { className: 'loading', role: 'status' })); }
function table(container, columns, rows, emptyMessage) {
  if (!rows.length) { empty(container, emptyMessage); return; }
  const grid = el('table');
  const thead = el('thead'); const headrow = el('tr');
  for (const column of columns) headrow.append(el('th', column.title, { scope: 'col' }));
  thead.append(headrow); grid.append(thead);
  const tbody = el('tbody');
  for (const row of rows) {
    const tr = el('tr');
    for (const column of columns) {
      const td = el('td', null, column.className ? { className: column.className } : {});
      const value = column.render ? column.render(row) : row[column.key];
      if (value instanceof Node) td.append(value); else td.textContent = text(value);
      tr.append(td);
    }
    tbody.append(tr);
  }
  grid.append(tbody); container.replaceChildren(grid);
}
async function busy(buttonNode, callback, label = '처리 중…') {
  const old = buttonNode.textContent;
  buttonNode.disabled = true; buttonNode.textContent = label;
  try { return await callback(); } finally { buttonNode.disabled = false; buttonNode.textContent = old; }
}
function showLogin() {
  $('app-shell').hidden = true; $('login-screen').hidden = false;
  if ($('app-dialog').open) $('app-dialog').close();
}
function applySession(session) {
  state.session = session;
  if (!session.user) { showLogin(); return; }
  $('login-screen').hidden = true; $('app-shell').hidden = false;
  $('user-name').textContent = session.user.name || session.user.login_id;
  $('user-role').textContent = labels[session.user.role] || session.user.role;
  for (const element of document.querySelectorAll('[data-admin]')) element.hidden = !admin();
  $('loan-nav-label').textContent = admin() ? '대출 · 반납' : '내 대출 내역';
  $('loans-title').textContent = admin() ? '대출 · 반납' : '내 대출 내역';
  $('dashboard-greeting').textContent = admin() ? '오늘의 도서와 대출 현황을 확인하세요.' : `${session.user.name || session.user.login_id}님의 대출 현황을 확인하세요.`;
  const settings = session.settings || {};
  $('today-label').textContent = `기준일 ${settings.reference_date || dateInput(new Date())}`;
  $('policy-note').textContent = `과제 운영 설정: 최대 ${settings.max_loans ?? 3}권 · 대출 기간 ${settings.loan_days ?? 14}일. 연체 중이거나 이용 정지 상태이면 새로 대출할 수 없습니다.`;
  const integrations = session.integrations || {};
  $('book-api-status').textContent = integrations.nl_books ? '인증키 설정됨' : '인증키 설정 필요';
  $('book-api-status').className = `badge ${integrations.nl_books ? '' : 'warning'}`;
  $('book-api-notice').hidden = !!integrations.nl_books;
  const local = integrations.provider === 'ollama';
  $('openai-status').textContent = local ? 'Ollama · ' + integrations.ollama_model : integrations.openai ? 'API 키 설정됨' : 'API 키 설정 필요';
  $('openai-status').className = `badge ${local || integrations.openai ? '' : 'warning'}`;
  $('openai-notice').hidden = local || !!integrations.openai;
  const links = admin() ? [['books', '도서 등록·조회'], ['loans', '대출·반납 처리'], ['external', 'API로 도서 찾기'], ['nl2sql', '자연어로 조회']] : [['books', '도서 찾아보기'], ['loans', '내 대출 확인']];
  $('quick-actions').replaceChildren(...links.map(([page, title]) => { const a = el('a', title, { href: `#${page}` }); a.append(el('span', '→', { 'aria-hidden': 'true' })); return a; }));
  navigate();
}

async function navigate() {
  if (!state.session?.user) return;
  let page = location.hash.slice(1) || 'dashboard';
  if (!pages.includes(page) || (!admin() && ['members', 'external', 'nl2sql'].includes(page))) page = 'dashboard';
  state.page = page;
  for (const name of pages) $(`page-${name}`).hidden = name !== page;
  for (const link of document.querySelectorAll('[data-page]')) {
    if (link.dataset.page === page) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  }
  document.title = `${{ dashboard: '대시보드', books: '도서 목록', members: '회원 관리', loans: '대출 · 반납', external: '외부 도서 등록', nl2sql: '자연어 SQL 조회' }[page]} | 책담`;
  try {
    if (page === 'dashboard') await loadDashboard();
    if (page === 'books') await loadBooks();
    if (page === 'members') await loadMembers();
    if (page === 'loans') await loadLoans();
  } catch (error) { showToast(error.message, true); }
}
async function loadDashboard() {
  loading($('dashboard-loans'));
  const [stats, loanData] = await Promise.all([api('/api/dashboard'), api('/api/loans?filter=active')]);
  const cards = admin() ? [
    ['등록 도서', stats.total_books, '종', `총 소장 도서 ${stats.total_copies ?? 0}권`, false],
    ['대출 가능', stats.available_copies, '권', '정상 상태의 미대출 소장 도서', false],
    [admin() ? '현재 대출' : '내 대출', stats.active_loans, '건', '반납되지 않은 대출', false],
    [admin() ? '현재 연체' : '내 연체', stats.overdue_loans, '건', '반납 예정일이 지난 미반납 건', true]
  ] : [
    ['전체 대출 이력', stats.total_loans, '건', '지금까지 이용한 대출 기록', false],
    ['반납 완료', stats.returned_loans, '건', '반납 처리가 완료된 대출', false],
    ['내 대출', stats.active_loans, '건', '현재 반납하지 않은 도서', false],
    ['내 연체', stats.overdue_loans, '건', '반납 예정일이 지난 미반납 건', true]
  ];
  $('stats-grid').replaceChildren(...cards.map(([label, value, unit, detail, warning]) => {
    const card = el('article', null, { className: `stat-card ${warning ? 'warning-stat' : ''}` });
    const number = el('div', Number(value || 0).toLocaleString('ko-KR'), { className: 'stat-value' }); number.append(el('small', unit));
    card.append(el('p', label, { className: 'stat-label' }), number, el('p', detail, { className: 'stat-detail' })); return card;
  }));
  table($('dashboard-loans'), [
    { title: '도서', key: 'title' },
    ...(admin() ? [{ title: '회원', render: (r) => r.member_name || r.name }] : []),
    { title: '반납 예정일', render: (r) => displayDate(r.due_date) },
    { title: '상태', render: loanBadge }
  ], items(loanData).slice(0, 6), '진행 중인 대출이 없습니다.');
}
let catalogRevision = 0;
async function loadBooks() {
  const revision = ++catalogRevision;
  loading($('books-table'));
  const query = new URLSearchParams(new FormData($('book-search')));
  const response = query.get('q').trim()
    ? await api('/api/books/search', 'POST', Object.fromEntries(query))
    : await api(`/api/books?${query}`);
  if (revision !== catalogRevision) return;
  if (response.notice) showToast(response.notice);
  const rows = items(response);
  $('book-count').textContent = `${rows.length}종`;
  table($('books-table'), [
    { title: '번호', key: 'book_id' },
    { title: '도서명', render: (r) => {
      const wrap = el('div'); wrap.append(button(r.title, () => showBook(r.book_id), 'table-link'));
      wrap.append(el('span', r.isbn ? `ISBN ${r.isbn}` : 'ISBN 미등록', { className: 'table-subtitle' })); return wrap;
    } },
    { title: '저자', key: 'author' }, { title: '출판사', key: 'publisher' }, { title: '분류', key: 'category' },
    { title: '정상 소장', render: (r) => `${r.normal_count ?? 0}권` },
    { title: '대출 가능', render: (r) => badge(Number(r.available_count) > 0 ? 'NORMAL' : 'DISCARDED', `${r.available_count ?? 0}권`) },
    ...(!admin() ? [{ title: '신청', render: r => Number(r.available_count) > 0 ? button('대출 신청', () => showBook(r.book_id)) : '—' }] : [])
  ], rows, '검색 결과가 없습니다.');
}
async function loadMembers() {
  loading($('members-table'));
  const rows = items(await api(`/api/members?q=${encodeURIComponent($('member-query').value)}`));
  $('member-count').textContent = `${rows.length}명`;
  table($('members-table'), [
    { title: '회원번호', key: 'member_id' }, { title: '이름', key: 'name' }, { title: '연락처', key: 'phone' },
    { title: '가입일', render: (r) => displayDate(r.join_date) }, { title: '상태', render: (r) => badge(r.status) },
    { title: '관리', render: (r) => actions(button('수정', () => memberForm(r)), ...(r.account_id || r.login_id ? [el('span', '계정 연결됨', { className: 'badge neutral' })] : [button('계정 발급', () => accountForm(r))])) }
  ], rows, '등록된 회원이 없습니다.');
}
function overdueDays(row) {
  if (row.return_date) return 0;
  if (row.overdue_days !== undefined && row.overdue_days !== null) return Math.max(0, Number(row.overdue_days));
  const today = state.session?.settings?.reference_date || dateInput(new Date());
  return Math.max(0, Math.round((new Date(`${today}T00:00:00`) - new Date(`${dateInput(row.due_date)}T00:00:00`)) / 86400000));
}
function loanBadge(row) { return row.return_date ? badge('RETURNED', '반납 완료') : overdueDays(row) > 0 ? badge('OVERDUE', `연체 ${overdueDays(row)}일`) : badge('NORMAL', '대출 중'); }
async function loadLoans() {
  loading($('loans-table'));
  const query = new URLSearchParams({ filter: state.loanFilter });
  const memberId = $('loan-member-id').value;
  if (admin() && memberId) query.set('member_id', memberId);
  const [history, requests] = await Promise.all([api(`/api/loans?${query}`), api('/api/loan-requests')]);
  const rows = items(history);
  table($('requests-table'), [
    { title: '도서', key: 'title' }, { title: '관리번호', key: 'owned_id' },
    ...(admin() ? [{ title: '회원', key: 'member_name' }] : []),
    { title: '상태', render: r => ({PENDING:'승인 대기', APPROVED:'승인 완료', DECLINED:'거절'}[r.status]) },
    ...(admin() ? [{ title: '처리', render: r => r.status !== 'PENDING' ? '—' : actions(
      ...[['승인','approve'],['거절','decline']].map(([label, decision]) => button(label, async () => {
        await api(`/api/loan-requests/${r.request_id}/${decision}`, 'POST', {});
        await loadLoans();
      }))) }] : [])
  ], items(requests), '대출 신청 내역이 없습니다.');
  $('loan-count').textContent = `${rows.length}건`;
  table($('loans-table'), [
    { title: '번호', key: 'loan_id' }, { title: '도서', render: (r) => {
      const wrap = el('div', r.title); wrap.append(el('span', r.owned_id, { className: 'table-subtitle' })); return wrap;
    } },
    ...(admin() ? [{ title: '회원', render: (r) => r.member_name || r.name || r.member_id }] : []),
    { title: '대출일', render: (r) => displayDate(r.loan_date) }, { title: '반납 예정', render: (r) => displayDate(r.due_date) },
    { title: '실제 반납', render: (r) => displayDate(r.return_date) }, { title: '상태', render: loanBadge },
    ...(admin() ? [{ title: '처리', render: (r) => r.return_date ? '—' : button('반납', () => confirmReturn(r)) }] : [])
  ], rows, '해당하는 대출 내역이 없습니다.');
}

function openDialog(title) {
  $('dialog-title').textContent = title;
  $('dialog-content').replaceChildren();
  if (!$('app-dialog').open) $('app-dialog').showModal();
  return $('dialog-content');
}
function closeDialog() { $('app-dialog').close(); }
function formDialog(title, fields, onSubmit, options = {}) {
  const content = openDialog(title);
  const form = el('form', null, { className: 'dialog-form' });
  if (options.note) form.append(el('p', options.note, { className: 'notice' }));
  for (const field of fields) {
    const wrapper = el('div', null, { className: `form-field ${field.full ? 'full-width' : ''}` });
    const id = `edit-${field.name}`;
    wrapper.append(el('label', field.label + (field.required ? ' *' : ''), { for: id }));
    const input = el(field.options ? 'select' : 'input', null, { id, name: field.name });
    if (field.options) {
      for (const option of field.options) input.append(el('option', option[1], { value: option[0] }));
    } else input.type = field.type || 'text';
    if (field.required) input.required = true;
    if (field.maxLength) input.maxLength = field.maxLength;
    if (field.minLength) input.minLength = field.minLength;
    if (field.min !== undefined) input.min = field.min;
    if (field.pattern) input.pattern = field.pattern;
    if (field.placeholder) input.placeholder = field.placeholder;
    if (field.readOnly) input.readOnly = true;
    if (field.type === 'password') input.autocomplete = 'new-password';
    input.value = field.value ?? '';
    wrapper.append(input);
    if (field.help) { const help = el('p', field.help, { id: `${id}-help`, className: 'form-help' }); input.setAttribute('aria-describedby', help.id); wrapper.append(help); }
    form.append(wrapper);
  }
  const error = el('p', null, { className: 'dialog-error', role: 'alert' }); error.hidden = true; form.append(error);
  const foot = el('div', null, { className: 'dialog-actions' });
  const save = el('button', options.submitLabel || '저장', { type: 'submit', className: 'button primary' });
  foot.append(button('취소', closeDialog, 'button secondary'), save); form.append(foot);
  form.addEventListener('submit', async (event) => {
    event.preventDefault(); error.hidden = true;
    const payload = Object.fromEntries(new FormData(form));
    try { await busy(save, () => onSubmit(payload)); }
    catch (failure) { error.textContent = failure.message; error.hidden = false; }
  });
  content.append(form);
  form.querySelector('input:not([readonly]),select')?.focus();
  return form;
}
const bookFields = (book = {}) => [
  { name: 'title', label: '도서명', value: book.title, required: true, maxLength: 200, full: true },
  { name: 'isbn', label: 'ISBN', value: book.isbn, maxLength: 13, pattern: '[0-9]{13}', help: '선택 입력 · 하이픈 없이 13자리 숫자' },
  { name: 'category', label: '분류', value: book.category, maxLength: 50, placeholder: '예: 컴퓨터공학' },
  { name: 'author', label: '저자', value: book.author, maxLength: 100 },
  { name: 'publisher', label: '출판사', value: book.publisher, maxLength: 100 },
  { name: 'pub_date', label: '실제 출판일', type: 'date', value: dateInput(book.pub_date), help: '확인되지 않은 날짜는 비워 두세요.' }
];
function bookForm(book = {}) {
  formDialog(book.book_id ? '도서정보 수정' : '새 도서 등록', bookFields(book), async (payload) => {
    for (const key of ['isbn', 'author', 'publisher', 'pub_date', 'category']) if (!payload[key]) payload[key] = null;
    await api(book.book_id ? `/api/books/${book.book_id}` : '/api/books', book.book_id ? 'PATCH' : 'POST', payload);
    closeDialog(); showToast('도서정보를 저장했습니다.'); await refreshCurrent();
  });
}
async function showBook(id) {
  const content = openDialog('도서 상세'); loading(content);
  try {
    const book = await api(`/api/books/${id}`);
    $('dialog-title').textContent = book.title;
    content.replaceChildren();
    const summary = el('div', null, { className: 'book-summary' });
    for (const [label, value] of [['ISBN', book.isbn], ['저자', book.author], ['출판사', book.publisher], ['출판일', displayDate(book.pub_date)], ['분류', book.category], ['자료 출처', book.metadata_source === 'NL_ISBN' ? '국립중앙도서관 ISBN API' : book.metadata_source === 'SAMPLE' ? '과제용 샘플' : '직접 입력']]) {
      const cell = el('div'); cell.append(el('span', label), el('strong', text(value))); summary.append(cell);
    }
    content.append(summary);
    if (admin()) content.append(actions(button('도서정보 수정', () => bookForm(book)), button('+ 소장 도서 추가', () => copyForm(book), 'button primary small')));
    const heading = el('div', null, { className: 'detail-heading' }); heading.append(el('h3', `소장 도서 ${(book.copies || []).length}권`)); content.append(heading);
    const container = el('div', null, { className: 'table-container' }); content.append(container);
    table(container, [
      { title: '관리번호', key: 'owned_id' }, { title: '보관 위치', key: 'location' }, { title: '상태', render: (r) => badge(r.status) },
      { title: '대출 여부', render: (r) => badge(canBorrow(r) ? 'NORMAL' : 'DISCARDED', canBorrow(r) ? '대출 가능' : r.active_loan_id || r.loan_id || r.status === 'NORMAL' ? '대출 중' : '대출 불가') },
      ...(admin() ? [{ title: '관리', render: (r) => actions(button('수정', () => copyForm(book, r)), ...(canBorrow(r) ? [button('대출', () => loanForm(r.owned_id))] : [])) }] : [
        { title: '신청', render: r => r.requested ? '승인 대기' : !canBorrow(r) ? '—' :
          button('대출 신청', async () => {
            await api('/api/loan-requests', 'POST', {owned_id:r.owned_id});
            await showBook(id);
            showToast('대출을 신청했습니다. 내 대출 내역에서 승인 상태를 확인하세요.');
          }) }])
    ], book.copies || [], '등록된 소장 도서가 없습니다.');
  } catch (error) { content.replaceChildren(el('p', error.message, { className: 'error-inline' })); }
}
function copyForm(book, copy = {}) {
  formDialog(copy.owned_id ? '소장 도서 수정' : '소장 도서 추가', [
    { name: 'owned_id', label: '관리번호', value: copy.owned_id, required: true, maxLength: 20, readOnly: !!copy.owned_id, placeholder: '예: B0021' },
    { name: 'location', label: '보관 위치', value: copy.location, required: true, maxLength: 50, placeholder: '예: 공학자료실 A-01' },
    { name: 'status', label: '소장 상태', value: copy.status || 'NORMAL', options: [['NORMAL', '정상'], ['LOST', '분실'], ['DISCARDED', '폐기']] }
  ], async (payload) => {
    payload.book_id = book.book_id;
    await api(copy.owned_id ? `/api/copies/${encodeURIComponent(copy.owned_id)}` : '/api/copies', copy.owned_id ? 'PATCH' : 'POST', payload);
    showToast('소장 도서를 저장했습니다.'); await showBook(book.book_id); await refreshCurrent();
  }, { note: `대상 도서: ${book.title}. 관리번호는 개별 책마다 다르게 부여합니다.` });
}
function memberForm(member = {}) {
  formDialog(member.member_id ? '회원정보 수정' : '새 회원 등록', [
    { name: 'name', label: '이름', value: member.name, required: true, maxLength: 50 },
    { name: 'phone', label: '연락처', value: member.phone, required: true, maxLength: 20, type: 'tel', placeholder: '010-0000-0000' },
    { name: 'status', label: '회원 상태', value: member.status || 'ACTIVE', options: [['ACTIVE', '정상'], ['SUSPENDED', '이용 정지'], ['WITHDRAWN', '탈퇴']], help: '미반납 도서가 있으면 탈퇴할 수 없습니다.' }
  ], async (payload) => {
    await api(member.member_id ? `/api/members/${member.member_id}` : '/api/members', member.member_id ? 'PATCH' : 'POST', payload);
    closeDialog(); showToast('회원정보를 저장했습니다.'); await refreshCurrent();
  });
}
function accountForm(member) {
  formDialog('회원 로그인 계정 발급', [
    { name: 'login_id', label: '로그인 아이디', required: true, maxLength: 30 },
    { name: 'password', label: '초기 비밀번호', required: true, type: 'password', minLength: 8, maxLength: 128, help: '8자 이상 입력하세요.' }
  ], async (payload) => {
    await api('/api/accounts', 'POST', { ...payload, role: 'MEMBER', member_id: member.member_id });
    closeDialog(); showToast('회원 계정을 발급했습니다.'); await refreshCurrent();
  }, { note: `${member.name} 회원(번호 ${member.member_id})의 로그인 계정입니다. 회원 한 명에 계정을 하나만 연결할 수 있습니다.`, submitLabel: '계정 발급' });
}
async function loanForm(ownedId = '') {
  const members = items(await api('/api/members'));
  formDialog('도서 대출 처리', [
    { name: 'member_id', label: '대출 회원', required: true, options: [['', '회원을 선택하세요'], ...members.map((m) => [String(m.member_id), `${m.name} (#${m.member_id}) · ${labels[m.status] || m.status}`])], full: true },
    { name: 'owned_id', label: '소장 도서 관리번호', value: ownedId, required: true, maxLength: 20, full: true, placeholder: '예: B0001', help: '도서번호나 ISBN이 아닌, 개별 책의 관리번호를 입력하세요.' }
  ], async (payload) => {
    await api('/api/loans', 'POST', { member_id: Number(payload.member_id), owned_id: payload.owned_id });
    closeDialog(); showToast('대출을 처리했습니다.'); await refreshCurrent();
  }, { note: `회원 상태·대출 한도·연체 여부·책 상태를 확인합니다. 반납 예정일은 현재 설정(${state.session?.settings?.loan_days ?? 14}일)에 따라 자동 계산합니다.`, submitLabel: '대출 처리' });
}
function confirmReturn(loan) {
  const content = openDialog('반납 확인');
  content.append(el('p', `「${loan.title || loan.owned_id}」을 반납 처리할까요?`), el('p', `관리번호 ${loan.owned_id} · 대출번호 ${loan.loan_id}`, { className: 'muted small-text' }));
  const foot = el('div', null, { className: 'dialog-actions' });
  const submit = button('반납 처리', async () => busy(submit, async () => { await api(`/api/loans/${loan.loan_id}/return`, 'POST', {}); closeDialog(); showToast('반납을 처리했습니다.'); await refreshCurrent(); }), 'button primary');
  foot.append(button('취소', closeDialog, 'button secondary'), submit); content.append(foot);
}
async function refreshCurrent() {
  if (state.page === 'dashboard') await loadDashboard();
  if (state.page === 'books') await loadBooks();
  if (state.page === 'members') await loadMembers();
  if (state.page === 'loans') await loadLoans();
}
function externalImportForm(book) {
  const fields = bookFields({ ...book, pub_date: null });
  formDialog('API 검색 결과 등록', fields, async (payload) => {
    for (const key of ['isbn', 'pub_date', 'author', 'publisher', 'category']) if (!payload[key]) payload[key] = null;
    await api('/api/external/import', 'POST', { ...book, ...payload });
    closeDialog(); showToast('API 도서정보를 등록했습니다. 소장 도서는 도서 상세에서 추가하세요.');
  }, { note: `국립중앙도서관 서지정보입니다. 출판예정일: ${displayDate(book.planned_pub_date)}. 실제 출판일은 확인한 경우에만 입력하세요.`, submitLabel: '도서 등록' });
}
async function externalSearch(event) {
  event.preventDefault();
  const form = event.currentTarget; const submit = form.querySelector('button[type=submit]');
  await busy(submit, async () => {
    loading($('external-table'));
    try {
      const result = await api(`/api/external/books?${new URLSearchParams(new FormData(form))}`);
      const rows = items(result); $('external-count').textContent = `${rows.length}건`;
      table($('external-table'), [
        { title: '도서명', key: 'title' }, { title: '저자', key: 'author' }, { title: '출판사', key: 'publisher' },
        { title: 'ISBN', key: 'isbn' }, { title: '출판예정일', render: (r) => displayDate(r.planned_pub_date) },
        { title: '등록', render: (r) => button('선택', () => externalImportForm(r), 'button primary small') }
      ], rows, '검색된 도서가 없습니다.');
    } catch (error) { $('external-table').replaceChildren(el('p', error.message, { className: 'error-inline', role: 'alert' })); $('external-count').textContent = ''; }
  }, '검색 중…');
}
async function runNaturalQuery(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector('button[type=submit]');
  $('nl2sql-results').hidden = true;
  state.sql = '';
  try {
    const data = await busy(submit, () => api('/api/nl2sql', 'POST', Object.fromEntries(new FormData(form))), 'SQL 생성·조회 중…');
    state.sql = data.generated_sql || data.sql || ''; $('generated-sql').textContent = state.sql;
    const mode = data.prompt_mode === 'simple' ? '기본 스키마' : '스키마·업무 규칙 보강';
    $('sql-meta').textContent = `${data.model || 'LLM'} · ${mode} · ${((data.elapsed_ms || 0) / 1000).toFixed(2)}초 · 조회 전용${data.truncated ? ' · 결과가 200행을 초과하여 일부만 표시합니다.' : ''}`;
    const rows = data.rows || [];
    const columns = data.columns || (rows.length && !Array.isArray(rows[0]) ? Object.keys(rows[0]) : []);
    $('result-count').textContent = `${rows.length}행`;
    table($('result-table'), columns.map((name, i) => ({ title: name, render: (row) => { const value = Array.isArray(row) ? row[i] : row[name]; return typeof value === 'object' && value !== null ? JSON.stringify(value) : value; } })), rows, '조건에 맞는 결과가 없습니다.');
    $('nl2sql-results').hidden = false;
  } catch (error) { showToast(error.message, true); }
}

$('loan-member-filter').addEventListener('submit', (event) => {
  event.preventDefault();
  loadLoans().catch((error) => showToast(error.message, true));
});
$('signup-open').addEventListener('click', () => {
  formDialog('회원가입', [
    { name: 'name', label: '이름', required: true, maxLength: 50 },
    { name: 'phone', label: '연락처', type: 'tel', required: true, maxLength: 20 },
    { name: 'login_id', label: '아이디', required: true, maxLength: 30, full: true },
    { name: 'password', label: '비밀번호', type: 'password', required: true, minLength: 8, maxLength: 128, help: '8~128자' },
    { name: 'confirm', label: '비밀번호 확인', type: 'password', required: true }
  ], async ({ confirm, ...data }) => {
    if (data.password !== confirm) throw new Error('비밀번호가 일치하지 않습니다.');
    const account = await api('/api/register', 'POST', data);
    closeDialog();
    $('dialog-content').replaceChildren();
    $('login-id').value = account.login_id;
    $('login-password').value = '';
    $('login-error').hidden = true;
    $('login-password').focus();
    showToast('회원가입이 완료되었습니다. 새 계정으로 로그인해 주세요.');
  }, { submitLabel: '가입하기' });
});

$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const form = event.currentTarget;
  $('login-error').hidden = true;
  try {
    const result = await busy(form.querySelector('button[type=submit]'), () => api('/api/login', 'POST', Object.fromEntries(new FormData(form))), '로그인 중…');
    form.reset(); applySession(result);
  } catch (error) { $('login-error').textContent = error.message; $('login-error').hidden = false; }
});
$('logout-button').addEventListener('click', async () => {
  try { const response = await api('/api/logout', 'POST', {}); applySession(response?.csrf_token ? response : await api('/api/session')); } catch (error) { showToast(error.message, true); }
});
window.addEventListener('hashchange', navigate);
$('dialog-close').addEventListener('click', closeDialog);
$('app-dialog').addEventListener('click', (event) => { if (event.target === $('app-dialog')) { const r = event.target.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) closeDialog(); } });
$('book-search').addEventListener('submit', (event) => { event.preventDefault(); loadBooks().catch((e) => showToast(e.message, true)); });
$('book-search').addEventListener('reset', () => setTimeout(() => loadBooks().catch((e) => showToast(e.message, true)), 0));
$('member-search').addEventListener('submit', (event) => { event.preventDefault(); loadMembers().catch((e) => showToast(e.message, true)); });
$('loan-filters').addEventListener('click', (event) => {
  const node = event.target.closest('[data-filter]'); if (!node) return;
  state.loanFilter = node.dataset.filter;
  for (const item of $('loan-filters').querySelectorAll('button')) item.setAttribute('aria-pressed', String(item === node));
  loadLoans().catch((e) => showToast(e.message, true));
});
$('add-book').addEventListener('click', () => bookForm());
$('add-member').addEventListener('click', () => memberForm());
$('add-loan').addEventListener('click', () => loanForm().catch((e) => showToast(e.message, true)));
$('external-search').addEventListener('submit', externalSearch);
$('nl2sql-form').addEventListener('submit', runNaturalQuery);
document.querySelectorAll('[data-question]').forEach((node) => node.addEventListener('click', () => { $('natural-question').value = node.dataset.question; $('natural-question').focus(); }));
$('copy-sql').addEventListener('click', async () => { try { await navigator.clipboard.writeText(state.sql); showToast('SQL을 복사했습니다.'); } catch { showToast('복사할 수 없습니다. SQL 내용을 직접 선택해 복사하세요.', true); } });
api('/api/session').then(applySession).catch((error) => { $('login-error').textContent = error.message; $('login-error').hidden = false; });
