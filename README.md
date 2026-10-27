# 병동 근무표 백엔드

FastAPI와 OR-Tools CP-SAT으로 구현한 단일 병동 관리자용 근무표 서비스다. 로그인과 역할별 화면 없이 병동 관리자 화면에서 간호사, 근무 규칙, 공휴일, 근무표와 자동 생성을 관리한다.

## 실행

```bash
uv sync
uv run python -m app.seed
uv run uvicorn app.main:app --reload
```

기본 API 경로는 `/api/v1`, Swagger 문서는 `http://localhost:8000/docs`다. `DATABASE_URL`이 없으면 `sqlite:///./nurs-local.db`를 사용한다. 기존 데이터베이스는 서버 시작 시 직급, 입사 연도, 프리셉터와 공유 링크 열을 자동으로 보완한다.

## 초기 데이터

`python -m app.seed`는 한 병동과 간호사 28명을 만든다.

- 수간호사 1명
- 차지 간호사 2명
- 일반 간호사 25명
- 프리셉터는 5~6년 차 일반 간호사에게만 별도 속성으로 지정
- 직급과 경력을 분리해 25년 차 일반 간호사와 23년 차 차지 간호사를 포함

초기 필요 인원 D 6명, E 5명, N 4명은 클라이언트 확인 전 임시값이다. 확정 질문과 현재 적용 규칙은 [도메인 규칙 및 Q&A](docs/domain-rules-and-client-qa.md)에 기록했다.

## 주요 API

모든 관리자 API는 로그인 없이 사용한다. 내부 감사 로그에는 시드로 생성한 병동 관리자 ID를 작업 주체로 기록한다.

- `GET /wards/me`
- `GET|POST /wards/me/nurses`
- `GET|PATCH /wards/me/nurses/{nurseId}`
- `POST /wards/me/nurses/{nurseId}/retire`
- `GET|PATCH /wards/me/rules...`
- `GET|PUT|DELETE /wards/me/holidays...`
- `GET|POST /wards/me/schedules...`
- `POST /wards/me/schedules/{scheduleId}/generations`
- `POST /wards/me/schedules/{scheduleId}/share`
- `GET /public/schedules/{token}`

확정된 근무표만 공유할 수 있다. 공유 API를 다시 호출하면 이전 링크는 만료되며, 공개 API의 모든 셀은 읽기 전용이다.

로그인, 회원가입, 병동 가입 승인, 일반 간호사 온라인 오프 신청, 개인 알림 API는 제거했다. 연차와 오프 요청 테이블은 과거 데이터 호환을 위해 남아 있지만 외부 API에서는 접근할 수 없다.

## 간호사 모델

외부 API는 다음 필드를 사용한다.

- `rank`: `HEAD_NURSE | CHARGE_NURSE | STAFF_NURSE`
- `hireYear`: 입사 연도
- `isPreceptor`: 프리셉터 역할 속성
- `status`, `skillLevel`, `affiliationStart`, `affiliationEnd`, `preceptorOf`

수간호사는 병동에 정확히 1명만 유지한다. 프리셉터는 현재 연도 기준 입사 후 5~6년인 일반 간호사만 지정할 수 있다. 차지 간호사가 2명이 아니면 간호사 변경 응답의 `warnings`에 표시한다.

## 규칙 엔진

규칙의 활성 여부, 강도와 파라미터를 검증기와 생성기가 함께 사용한다. 현재 확정되지 않은 차지 조별 배치, 고연차 조별 배치, 프리셉터 동반 규칙은 기본 비활성이다.

- 최대 연속 근무 기본값 5일
- 최대 연속 N 기본값 3일
- N 블록 종료 뒤 다음 날 OFF는 하드 규칙
- 두 번째 OFF는 소프트 규칙
- 임신 중 N 금지
- D/E/N 필요 인원은 `COVERAGE.parameters`로 관리

## 테스트

```bash
uv run pytest
```

테스트는 임시 SQLite 데이터베이스에 매번 28명 시드를 구성하고, 제거된 API, 간호사 구조, 파라미터 규칙, 자동 생성과 공개 공유 링크를 검증한다.
