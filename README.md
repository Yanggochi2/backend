# 병동 근무표 백엔드

Spring Boot 4 / Java 17 / PostgreSQL. 기능 명세 `function.md` v0.2 기준.

## 실행
```bash
docker compose up -d                                   # PostgreSQL (nurs/nurs)
./gradlew bootRun                                       # 운영 설정 (Secure 쿠키)
./gradlew bootRun --args='--spring.profiles.active=local'  # H2 인메모리, http 로컬 개발용
./gradlew test
```

### API E2E 점검 (QA)
빈 DB로 띄운 서버를 상대로 전체 시나리오(99개 항목)를 확인한다. 실패가 있으면 종료 코드 1.
```bash
java -jar build/libs/backend-0.0.1-SNAPSHOT.jar --spring.profiles.active=local --server.port=18080 "--reminder.cron=*/10 * * * * *" &
scripts/e2e.sh 18080
```

## 구조 (3-Layered, CLAUDE.md)
- `presentation` 컨트롤러·세션 인터셉터·에러 변환
- `business` 서비스 (권한·병동 범위 검증, 감사 로그)
- `persistence` JPA 엔티티·리포지토리
- `domain` 순수 규칙: `ScheduleValidator`(SCH-06), `ScheduleGenerator`(GEN-01)

## 보안 원칙 (SEC-03)
- 인증은 세션 쿠키(HttpOnly, Secure, SameSite=Strict, 30분). 토큰을 body/쿼리로 주고받지 않음
- 병동 ID는 어떤 경로에도 없음. 항상 세션 사용자의 소속 병동이 대상
- 타 병동 리소스 → 404, 권한 부족 → 403. 일반 간호사 응답 필드는 서버에서 제거

## API (`/api`)
| 메서드 | 경로 | 기능 | 권한 |
|---|---|---|---|
| POST | /auth/signup, /auth/login, /auth/logout | AUTH-01·05 | - |
| GET | /me | 내 정보·소속 | 로그인 |
| POST | /wards | 병동 개설, 개설자=수간호사 (AUTH-00) | 소속 없음 |
| GET | /ward | 내 병동 (코드는 수간호사만) | 소속 |
| POST | /ward/join `{code}` | 가입 신청 (AUTH-03, 시간당 10회) | 소속 없음 |
| POST | /ward/code | 코드 재발급 (AUTH-04) | 수간호사 |
| GET | /ward/join-requests | 가입 대기 목록 | 수간호사 |
| POST | /ward/join-requests/{id}/approve·reject | AUTH-06 | 수간호사 |
| POST | /ward/head-transfer `{nurseId, mode: GRANT/TRANSFER}` | AUTH-07 | 수간호사 |
| GET/PUT | /rules, POST /rules/preset | RULE-01·02·05 | 조회 소속 / 수정 수간호사 |
| GET/POST/DELETE | /holidays | RULE-03 | 조회 소속 / 수정 수간호사 |
| GET | /nurses `?includeRetired&role&dutyRole&status&sort=name\|career&page&size` | NUR-03 | 소속 (필드 차등) |
| POST/PUT | /nurses, /nurses/{id} | NUR-01·04 → `{nurse, violations, warnings}` | 수간호사 |
| POST | /nurses/{id}/retire `{affiliationEnd}` | NUR-09 | 수간호사 |
| GET | /nurses/export `?includeRetired` | 간호사 명단 .xlsx (감사 로그 기록) | 수간호사 |
| POST | /nurses/import/preview (multipart `file`) | 명단 일괄 등록 1단계: 행별 `{form, errors, warnings}`, 저장 안 함 | 수간호사 |
| POST | /nurses/import/apply `[form, ...]` | 2단계: 미리보기의 form 배열을 그대로 전송, 하나라도 틀리면 전체 취소 | 수간호사 |
| POST/GET | /schedules/{yyyy-MM} | SCH-01 생성 / 조회 (간호사는 확정본만) | |
| PATCH | /schedules/{ym}/cells `[{nurseId,date,duty}]` | 일괄 편집, duty=null은 미배정 | 수간호사 |
| GET | /schedules/{ym}/violations | SCH-06 | 수간호사 |
| POST | /schedules/{ym}/generate `{fixed?:[{nurseId,date}]}` | GEN-01·05·06 `{solved, wishOffRate, violations}` | 수간호사 |
| POST | /schedules/{ym}/confirm, /unconfirm `{reason}` | SCH-12 (하드 위반 시 409+detail) | 수간호사 |
| PUT | /schedules/{ym}/off-target `{offTarget}` | RULE-04 수동 조정 (null=자동) | 수간호사 |
| POST/DELETE | /schedules/{ym}/lock `?force=true` | SCH-13 편집 잠금 (30분 무활동 해제, 편집·생성 시 자동 획득) | 수간호사 |
| GET | /schedules/{ym}/export | SCH-09 .xlsx (감사 로그 기록) | 수간호사 |
| POST | /schedules/{ym}/import/preview (multipart `file`) | SCH-11 1단계: 이름 매칭·코드 제안, 저장 안 함 | 수간호사 |
| POST | /schedules/{ym}/import/apply `[{nurseId,date,duty}]` | SCH-11 2단계: 반영 + 위반 목록 | 수간호사 |
| GET | /notifications/me, POST /notifications/me/{id}/read | REQ-06 알림함 | 로그인 |
| GET/PUT | /notifications/me/settings `{muted:[...]}` | 종류별 off | 로그인 |
| POST | /requests `{type, date, duty?, reasonCode?}` | REQ (ANNUAL_LEAVE/WISH_OFF/WISH_DUTY) | 소속 |
| GET | /requests/me, /requests | REQ-07 본인 / 관리 (기본 PENDING) | |
| DELETE | /requests/{id} | 본인 취소 (확정 전) | 소속 |
| POST | /requests/{id}/approve·reject `{reason}` | REQ-03 | 수간호사 |
| GET | /audit-logs `?from&to&actorUserId&action` | SEC-02 | 수간호사 |

## 엑셀 양식
- 근무표: 첫 행 = `이름`, `1`~`31`(또는 `1일`) / 첫 열 = 간호사 이름. 내보낸 파일을 그대로 다시 가져올 수 있음
- 간호사 명단: `이름, 역할, 듀티역할, 상태, 입사일, 경력(개월), 숙련도, 소속시작일, 소속종료일`
  - `역할`은 참고용(가져오기 시 무시), `소속종료일`은 선택
  - 듀티역할 `차지/프리셉터/신입/일반`, 상태 `재직/임신/휴직` (코드값 `CHARGE`, `ACTIVE` 등도 허용)
  - 날짜는 엑셀 날짜 셀 또는 `2026-09-01`, `2026.09.01`, `2026/09/01`

## 미구현 / 가정
- 미구현: 소셜 로그인(AUTH-02, 제공자 미정), 생성 진행률·중단(GEN-02, 생성이 동기라 불필요), 웹 푸시(알림은 앱 내 알림함 + 매일 18시 근무 전날 리마인드)
- 🔶 가정: 단일 병동 소속, 가입 승인 단계 있음, OFF 목표 = 공휴일 수 + 1, 사유 코드는 명세 예시값
- 역할(role) 변경은 권한 이관(AUTH-07)으로만 가능 — 간호사 수정 API는 role을 받지 않음
