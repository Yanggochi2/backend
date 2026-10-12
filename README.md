# 병동 근무표 API

회원가입부터 로그인, 병동 개설, 간호사 등록, 월 근무표 자동 편성까지 하나의 흐름으로 동작하는 API입니다. 외부 패키지 없이 Python 3.11 이상과 SQLite만 사용합니다.

## 사용자 흐름

```text
회원가입 → 로그인 → 병동 개설 → 간호사 추가 → 월 근무표 생성 → 자동 편성 → 결과 조회
```

로그인 성공 시 서버가 세션 쿠키를 발급합니다. 이후 데이터를 변경하는 요청은 로그인 응답의 `csrfToken`을 `X-CSRF-Token` 헤더로 함께 전송해야 합니다.

## 구현 범위

| 기능 | API |
| --- | --- |
| 회원가입과 로그인 | `POST /api/v1/auth/signup`, `POST /api/v1/auth/login` |
| 내 계정 조회와 로그아웃 | `GET /api/v1/me`, `POST /api/v1/auth/logout` |
| 병동 개설 | `POST /api/v1/wards` |
| 내 병동 조회 | `GET /api/v1/wards/me` |
| 간호사 등록 | `POST /api/v1/wards/me/nurses` |
| 간호사 목록과 상세 | `GET /api/v1/wards/me/nurses`, `GET /api/v1/wards/me/nurses/{nurseId}` |
| 간호사 수정과 퇴사 | `PATCH /api/v1/wards/me/nurses/{nurseId}`, `POST /api/v1/wards/me/nurses/{nurseId}/retire` |
| 빈 근무표 생성과 조회 | `POST /api/v1/wards/me/schedules`, `GET /api/v1/wards/me/schedules` |
| 셀 일괄 편집 | `PATCH /api/v1/wards/me/schedules/{scheduleId}/cells` |
| 커버리지와 위반 조회 | `GET .../coverage`, `GET .../violations` |
| 자동 편성 | `POST .../generations`, `GET /api/v1/wards/me/generations/{jobId}` |
| Excel 가져오기 | `POST .../import-previews`, `PATCH .../import-previews/{previewId}`, `POST .../apply` |
| 편집 잠금 | `POST .../lock`, `DELETE .../lock`, `POST .../lock/takeover` |
| 근무 신청 | `POST/GET /api/v1/wards/me/requests`, 승인·반려·취소 API |
| 규칙과 휴일 | `GET/PATCH .../rules`, 휴일·OFF 목표 API |
| 알림과 감사 | `/api/v1/me/notifications`, `/api/v1/wards/me/audit-logs` |

자동 편성은 듀티별 필요 인원, 차지의 D 전용 배정, 임신자의 N 금지, 신입 N 지원, 연속 N 3일 제한, 소속 기간을 반영합니다. 인력이 부족하면 결과를 저장하되 커버리지 하드 위반으로 반환합니다.

승인된 연차는 신청·승인 시점과 관계없이 자동 편성에서 `AL`로 고정됩니다. 승인된 희망 OFF는 필요 인원을 채울 수 있는 범위에서 `O`로 우선 배정됩니다. 인원 충족과 충돌하면 필요 인원을 우선합니다.

## 실행

```bash
cd backend
python3 -m ward_api.server
```

기본 주소는 `http://127.0.0.1:8000`이며 데이터베이스는 `backend/var/ward_scheduler.db`에 생성됩니다. 최초 실행 시 API를 바로 확인할 수 있는 데모 병동과 수간호사 계정도 함께 생성합니다.

## 전체 플로우 실행

서버를 실행한 상태에서 다른 터미널에 다음 명령을 입력합니다.

```bash
cd backend
python3 scripts/demo_flow.py
```

스크립트는 고유한 계정을 만든 후 전체 흐름을 순서대로 실행하고, 마지막에 병동 ID, 근무표 ID, 생성 상태, 셀 수, 하드 위반 수를 출력합니다. 다른 서버 주소를 사용할 때는 `python3 scripts/demo_flow.py http://서버주소`처럼 전달할 수 있습니다.

프론트엔드를 바로 체험할 데모 데이터를 준비하려면 서버 실행 전에 다음 명령을 한 번 실행합니다. 같은 명령을 다시 실행해도 데이터가 중복되지 않습니다.

```bash
python3 scripts/seed_frontend_demo.py
```

## 인증 요청 예시

```bash
# 회원가입
curl -X POST http://127.0.0.1:8000/api/v1/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"name":"관리자","email":"admin@example.com","password":"Secure!1234","termsAgreed":true}'

# 로그인 쿠키 저장. 응답의 csrfToken을 다음 요청에 사용
curl -c cookies.txt -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"Secure!1234"}'

# 병동 개설
curl -b cookies.txt -X POST http://127.0.0.1:8000/api/v1/wards \
  -H 'Content-Type: application/json' \
  -H 'X-CSRF-Token: 로그인_응답의_csrfToken' \
  -d '{"hospitalName":"예시병원","wardName":"A병동","requiredStaff":{"D":2,"E":2,"N":1},"rulePreset":"GENERAL"}'
```

## 간호사 추가 예시

```bash
curl -X POST http://127.0.0.1:8000/api/v1/wards/me/nurses \
  -H 'Content-Type: application/json' \
  -H 'X-CSRF-Token: 로그인_응답의_csrfToken' \
  -b cookies.txt \
  -d '{
    "name": "김간호사",
    "dutyRole": "GENERAL",
    "status": "ACTIVE",
    "joinedAt": "2024-01-01",
    "careerMonths": 36,
    "skillLevel": 3,
    "affiliationStart": "2024-01-01",
    "affiliationEnd": null,
    "preceptorOf": []
  }'
```

## 근무표 생성과 자동 편성

```bash
curl -X POST http://127.0.0.1:8000/api/v1/wards/me/schedules \
  -H 'Content-Type: application/json' \
  -H 'X-CSRF-Token: 로그인_응답의_csrfToken' \
  -b cookies.txt \
  -d '{"yearMonth":"2026-10"}'

curl -X POST http://127.0.0.1:8000/api/v1/wards/me/schedules/SCHEDULE_ID/generations \
  -H 'Content-Type: application/json' \
  -H 'X-CSRF-Token: 로그인_응답의_csrfToken' \
  -b cookies.txt \
  -d '{"fixedCells":[]}'
```

## 운영 설정

- `APP_ENV=production`: 데모 데이터와 개발용 인증 우회를 끄고, 보안 쿠키·JSON 로그·비동기 자동 편성을 기본으로 사용합니다.
- `WARD_ALLOWED_ORIGINS=https://app.example.com`: 접속을 허용할 프론트 도메인입니다. 운영 모드에서 필수입니다.
- `WARD_SECURE_COOKIES=1`: HTTPS 환경에서만 세션 쿠키를 전송합니다.
- `WARD_ASYNC_GENERATION=1`: 자동 편성을 백그라운드에서 실행합니다.
- `WARD_JSON_LOGS=1`: 요청 로그를 JSON 형식으로 출력합니다.
- `WARD_LOCK_TTL_SECONDS=1800`: 근무표 편집 잠금의 만료 시간을 설정합니다.
- `WARD_MAX_REQUEST_BYTES=11534336`: 요청 본문의 최대 크기를 설정합니다.
- `WARD_TRUST_PROXY_HEADERS=1`: 인증 프록시가 검증한 `X-User-Id`를 신뢰합니다. 외부 요청이 해당 헤더를 직접 넣을 수 없는 환경에서만 켜야 합니다.
- `WARD_SEED_DEMO=0`: 데모 계정과 병동 생성을 끕니다.
- `WARD_DEV_USER_ID=사용자_UUID`: 지정한 사용자를 개발용 요청 주체로 사용합니다.
- `HOST`, `PORT`, `WARD_DB_PATH`: 서버 주소, 포트, SQLite 파일 경로를 지정합니다.

Docker Compose, HTTPS, 데이터 백업 절차는 [운영 배포 문서](PRODUCTION.md)를 참고하세요.

## 테스트

```bash
python3 -m unittest discover -s tests -v
```
