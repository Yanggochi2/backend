# 병동 근무표 백엔드

간호사 근무표 자동 생성 서비스 백엔드. FastAPI / Python 3.12 / PostgreSQL. API 명세서 v1.0 기준.

## 빠른 실행 (Docker만 있으면 됨)
```bash
docker compose up --build -d     # PostgreSQL + API
```
- API 문서: http://localhost:8000/docs (Swagger), 기본 경로 `/api/v1`
- 로그: `docker compose logs -f api` / 중지: `docker compose down` / DB까지 초기화: `docker compose down -v`
- 데이터는 `pgdata` 볼륨에 남는다. 테이블은 서버 시작 시 자동 생성
- PostgreSQL 포트는 호스트에 열지 않는다(로컬 5432와 충돌 방지). 직접 접속: `docker compose exec postgres psql -U nurs`

## 로컬 개발 (uv)
```bash
uv sync                                         # 의존성 설치 (brew install uv)
uv run uvicorn app.main:app --reload            # SQLite 파일(nurs-local.db), http://localhost:8000/docs
```

| 환경 변수 | 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./nurs-local.db` | DB 연결 (compose에서는 PostgreSQL) |
| `COOKIE_SECURE` | `true` | 쿠키 Secure 속성. http 로컬이면 `false` (compose는 `false`) |
| `REMINDER_EVERY_SECONDS` | (없음 = 매일 18시 KST) | 근무 전날 리마인드 주기. 테스트용 |

## 테스트
```bash
uv run pytest     # 도메인 규칙 단위 테스트 + API 명세서 v1.0 시나리오 (TestClient, 임시 SQLite)
# PostgreSQL로 검증: 빈 DB를 TEST_DATABASE_URL로 지정
TEST_DATABASE_URL=postgresql+psycopg://user:pw@localhost:5432/empty_db uv run pytest
```

## 구조 (3-Layered, CLAUDE.md)
- `app/presentation` 라우트·요청 검증(schemas)·인증 쿠키·CSRF·Idempotency-Key·요청 단위 트랜잭션
- `app/business` 서비스 (권한·병동 범위 검증, 감사 로그, 알림, 엑셀, 자동 생성 작업)
- `app/persistence` SQLAlchemy 모델 (모든 식별자 UUID)
- `app/domain` 순수 규칙: `validator`(SCH-05 위반 검출), `generator`(GEN-01 자동 편성). FastAPI·DB에 의존하지 않는다

## API 계약 — 병동 근무표 서비스 API 명세서 v1.0
기본 경로 `/api/v1`, 59개 엔드포인트. 전체 목록은 서버 실행 후 `/docs`.

- **응답**: 단일 `{"data": {...}}`, 목록 `{"data": [...], "meta": {page, size, totalElements, totalPages}}`, 204는 본문 없음
- **오류**: `{"error": {code, message, traceId, status, timestamp, fieldErrors?, details?}}`. 응답 헤더 `X-Trace-Id`도 같은 값
- **인증**: 로그인 시 `ACCESS_TOKEN`(30분)·`REFRESH_TOKEN`(14일, Path=/api/v1/auth)·`CSRF_TOKEN` 쿠키. 토큰은 DB에 해시만 저장. `POST /auth/refresh`는 토큰 쌍을 회전
- **CSRF**: signup·login을 제외한 POST/PUT/PATCH/DELETE는 `X-CSRF-Token` 헤더 = `CSRF_TOKEN` 쿠키
- **병동 범위**: 경로는 `/wards/me/...`. 다른 병동 리소스·초안 근무표(일반 간호사)는 404, 역할 부족은 403
- **멱등성**: 모든 POST가 `Idempotency-Key`를 지원 (사용자별, 24시간). 같은 키·같은 요청은 저장된 2xx 응답 + `Idempotent-Replayed: true`, 다른 요청이면 422 `IDEMPOTENCY_KEY_REUSED`
- **동시성**: 근무표 변경은 `baseVersion` 불일치 시 409 `VERSION_CONFLICT`. 활성 편집 잠금이 있으면 `X-Schedule-Lock-Token`이 없거나 틀릴 때 423 `SCHEDULE_LOCKED`. 잠금이 없으면 baseVersion만으로 충돌을 막는다. 잠금 획득·해제는 근무표 version을 올리지 않는다

## 🔶 결정 필요 항목에 대한 현재 구현
| API | 선택한 동작 |
|---|---|
| AUTH-03 세션 갱신 | 리프레시 토큰 회전. 쓴 토큰은 즉시 폐기 |
| WARD-01 병동 개설 | requiredStaff D/E/N 각 0 이상·합계 1 이상, 아니면 422 `INVALID_STAFFING`. 가입 대기 중이면 409 |
| WARD-03 가입 신청 | 코드 대소문자 무시, 계정당 시간당 10회. 신청 상태는 `GET /me`의 `membershipRequest`로 조회 |
| WARD-05 가입 승인 | 본문 없음이 기본. 선택적으로 `{nurseId}`를 주면 계정 없는 기존 간호사 행에 연결 |
| RULE-06·07 OFF 목표 | 근무표가 있는 달만 (없으면 404 `TARGET_NOT_FOUND`). 자동값 = 공휴일 수 + 1, `targetCount: null`이면 자동으로 복귀 |
| RULE-05 공휴일 | 매년 같은 날짜의 공휴일만 기본 제공. 설·추석·대체공휴일은 병동이 보정 |
| SCH-05 커버리지 | 날짜×D/E/N, 미배정·소속 기간 밖 제외. status UNDER/MET/OVER |
| SCH-08 확정 취소 | 사유 필수, ARCHIVED는 409 |
| SCH-10 엑셀 가져오기 | 미리보기를 DB에 저장(202). 동명이인·미등록 행은 `nurseMappings`로 지정(null=건너뜀), 모르는 코드는 `dutyMappings`로 지정. AL·소속 기간 밖 셀은 가져오지 않음 |
| SCH-13·15 편집 잠금 | 30분 무활동 만료. 강제 인수는 사유 필수 + 기존 보유자 알림 + 감사 로그 |
| GEN-01·02 자동 생성 | 그리디 생성기가 즉시 끝나므로 요청 안에서 실행하고 끝난 작업을 202로 반환. 성공이면 근무표에 바로 반영, 하드 위반이 남으면 `NO_SOLUTION` + 충돌·완화안·부분 해(근무표는 그대로) |
| GEN-04 완화안 | 해당 HARD 규칙을 병동 설정에서 SOFT로 낮춘 뒤(감사 로그) 같은 조건으로 재실행. 법적 보호 규칙(임신 중 N, 소속 기간)은 불가 |
| REQ-01 신청 | 사유 코드 `PERSONAL/FAMILY/HEALTH/STUDY/ETC`(ETC는 상세 필수). 희망 오프는 1인 월 4일까지 |
| NOTI-01·04 알림 | 앱 내 알림함. `webPush: true`는 422 `PUSH_NOT_SUPPORTED` |

## 규칙 (RULE-01)
병동별 규칙 행: `COVERAGE`(parameters `{D,E,N}` = 필요 인원), `MAX_CONSECUTIVE_NIGHTS`·`MAX_CONSECUTIVE_WORK`(`{max}`), `NIGHT_TO_DAY`, `CHARGE_DAY_ONLY`, `PREGNANT_NIGHT`🔒, `NEW_NIGHT_ALONE`, `OUT_OF_AFFILIATION`🔒 (기본 HARD) / `OFF_TARGET`, `WISH_OFF_IGNORED`, `WISH_DUTY_IGNORED`, `PRECEPTOR_MISMATCH` (기본 SOFT). 🔒는 끄거나 SOFT로 낮출 수 없음. 꺼진 규칙은 위반에서 빠지고, 자동 생성은 켜진 HARD 규칙만 강제한다.

## 엑셀 양식
근무표: 첫 행 = `이름`, `1`~`31`(또는 `1일`) / 첫 열 = 간호사 이름. 내보낸 파일을 그대로 다시 가져올 수 있음

## 미구현 / 가정
- 미구현: 소셜 로그인, 웹 푸시(#27), 비동기 생성 작업 큐(#25). 테이블은 시작 시 자동 생성 — 운영 전 Alembic 도입(#24). **스키마가 바뀌었으므로 기존 `nurs-local.db`는 지우고 다시 띄울 것**
- 🔶 가정: 단일 병동 소속, 간호사 중복 판정 = 같은 이름 + 같은 입사일
- 역할(role) 변경은 권한 부여·이관(AUTH-07)으로만 가능 — 간호사 수정 API에 role을 보내면 400
