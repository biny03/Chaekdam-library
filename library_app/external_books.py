"""National Library of Korea bibliographic lookup. No simulated fallback."""
import json
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

class BookAPIError(ValueError):
    def __init__(self, message, code='BOOK_API_ERROR'):
        super().__init__(message)
        self.code = code

def normalize_isbn(value):
    value = re.sub(r'[\s-]', '', str(value or ''))
    if not re.fullmatch(r'\d{13}', value):
        raise BookAPIError('ISBN은 13자리 숫자로 입력해 주세요.', 'INVALID_ISBN')
    if sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(value)) % 10:
        raise BookAPIError('ISBN 검증 숫자가 맞지 않습니다.', 'INVALID_ISBN')
    return value

def map_record(record):
    """Publish-predate remains planned_pub_date, never actual pub_date."""
    isbn = str(record.get('EA_ISBN') or '').strip()
    try:
        isbn = normalize_isbn(isbn)
    except BookAPIError:
        isbn = None
    return {
        'isbn': isbn,
        'title': str(record.get('TITLE') or '').strip(),
        'author': str(record.get('AUTHOR') or '').strip(),
        'publisher': str(record.get('PUBLISHER') or '').strip(),
        'planned_pub_date': str(record.get('PUBLISH_PREDATE') or '').strip(),
        'pub_date': None,
        'category': None,
        'source_url': 'https://www.nl.go.kr/NL/contents/N31101030500.do',
        'metadata_source': 'NL_ISBN',
    }

def search_books(query, by, config):
    key = config.get('NL_API_KEY')
    if not key:
        raise BookAPIError('국립중앙도서관 인증키가 없습니다. .env의 NL_API_KEY를 입력한 후 재시작하세요.', 'MISSING_API_KEY')
    if by not in {'isbn', 'title'}:
        raise BookAPIError('검색 기준은 isbn 또는 title이어야 합니다.')
    query = str(query or '').strip()
    if not query or len(query) > 200:
        raise BookAPIError('검색어를 1~200자로 입력해 주세요.')
    if by == 'isbn':
        query = normalize_isbn(query)
    params = {'cert_key': key, 'result_style': 'json', 'page_no': 1, 'page_size': 20, by: query}
    request = Request('https://www.nl.go.kr/seoji/SearchApi.do?' + urlencode(params),
                      headers={'Accept': 'application/json', 'User-Agent': 'LibraryCourseProject/1.0'})
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise BookAPIError('응답이 너무 큽니다. ISBN으로 다시 검색해 주세요.')
        data = json.loads(raw.decode('utf-8-sig'))
    except (HTTPError, URLError, TimeoutError, OSError):
        # Do not expose URLs because the query string contains the API credential.
        raise BookAPIError('도서 API에 연결할 수 없습니다. 인증키와 네트워크를 확인해 주세요.') from None
    except (json.JSONDecodeError, UnicodeError):
        raise BookAPIError('도서 API 응답을 해석할 수 없습니다. 인증키와 서비스 상태를 확인해 주세요.') from None
    if not isinstance(data, dict) or not isinstance(data.get('docs'), list):
        raise BookAPIError('도서 API가 정상 목록을 반환하지 않았습니다. 인증키를 확인해 주세요.')
    items = [map_record(record) for record in data['docs'] if isinstance(record, dict)]
    return {'items': [item for item in items if item['title']], 'source': 'NL', 'total': data.get('TOTAL_COUNT')}
