# 확장형 B 설계와 평가 방법

관리자가 자연어 질문과 프롬프트 방식을 고르면 서버가 허용된 DB 스키마와 질문을
로컬 Ollama의 /api/generate에 보낸다. 응답에서 SQL을 추출한 뒤 SQLGlot으로 문장을
분석하고, 읽기 전용 계정으로 실행해 SQL과 결과 표를 함께 반환한다.
모델에 DB 행, 연락처, 로그인 정보, 비밀번호 해시는 보내지 않는다.

## 구현 경계

- `library_app/nl2sql.py`: 프롬프트 구성, 실제 모델 호출, SQL 검증, 제한된 실행과 결과 비교
- `evaluation/questions.json`: 난이도별 12개 평가 질문과 정답 SQL
- `scripts/evaluate_nl2sql.py`: 동일 스냅샷을 이용한 두 프롬프트 비교, JSON/CSV 저장
- `tests/test_nl2sql.py`: 허용/금지 SQL, API 응답 처리, 결과 비교의 자동 검증

기본 모델은 `qwen2.5-coder:3b`이다. `OLLAMA_MODEL`로 모델을 선택하며, 두 프롬프트 모두 동일한 모델과 생성 설정을 사용한다.
실제 모델 지원 여부와 API 요금은 사용 시점의 계정 및 공식 정책을 따른다.

## 실행 제한

관리자 전용 기능이다. SQL 검증은 단일 SELECT만 허용한다. 테이블은 book,
owned_book, member, loan으로 제한하고 member.phone과 전체 user_account 접근은
금지한다. SELECT *, CTE, UNION, SQL 주석, 시스템 테이블, 잠금 구문, 파일 입출력,
사용자 변수, 알려지지 않은 함수도 거부한다. 이 제한은 수업용 범위를 명확히 하기 위한
것이며 일반 SQL 분석 도구의 모든 SELECT 문법을 지원하려는 목적은 아니다.

SQL 문법 검사와 별도로 SELECT 권한만 가진 DB_READ_USER를 사용한다. 관리자 DB
계정으로 대체하지 않는다. MySQL의 읽기 전용 트랜잭션, 2초 실행 제한, 최대 200행
출력 제한을 적용한다. API 실패나 검증 실패 시 원본 SQL을 자동 수정해 다시 실행하지 않는다.

## 평가 해석

Simple은 기본 스키마와 공통 출력/안전조건을 받는다. Enhanced는 여기에 PK/FK와
업무 의미를 추가로 받는다. 공통 기준일을 날짜 리터럴로 제공하고 CURDATE()가
생성되더라도 실행 시 같은 기준일로 바꿔 날짜 변화로 인한 차이를 줄인다.

12개 정답 SQL의 실행 검증은 가능하지만, 이는 LLM 평가와 다르다. 선택한 모델로 비교 평가를 실행하기 전에는 모델의 정확도나 프롬프트 개선 효과를 주장하지 않는다.
실제 비교 후 실행 실패, 값 불일치, 행 수·중복·정렬 차이를 오류 사례로 분류한다.

## 참고 문서

- OpenAI 모델: https://developers.openai.com/api/docs/models/gpt-4.1-mini
- Responses API: https://developers.openai.com/api/reference/resources/responses/methods/create
- SQLGlot 프로젝트: https://github.com/tobymao/sqlglot

위 문서는 2026-11-23에 확인했다. 실제 API 호출은 사용자 키 입력 후 수행한다.
