# 규칙 엔진·병동 관리 API

기본 경로 `/api/v1`. 성공은 `{ "data": ... }`, 오류는 `{ "error": { "code", "message", "traceId", ... } }`.
인증 쿠키는 기존 인증 API를 사용한다. POST/PUT/PATCH/DELETE 요청은 `CSRF_TOKEN` 쿠키와 같은 값을
`X-CSRF-Token` 헤더에 넣는다. 병동과 권한은 세션에서 판정한다.

## 기능별 경로

| 기능 | Method / Path | 권한 |
| --- | --- | --- |
| 규칙 카탈로그·기본 파라미터 | GET `/wards/me/rule-catalog` | 수간호사 |
| 병동 규칙 조회 | GET `/wards/me/rules` | 수간호사 |
| 규칙 변경·재검증 | PATCH `/wards/me/rules/{ruleId}` | 수간호사 |
| 프리셋 적용 | POST `/wards/me/rule-presets/{STANDARD 또는 MINIMAL}/apply` | 수간호사 |
| 월 공휴일 조회 | GET `/wards/me/holidays?yearMonth=2026-11` | 수간호사 |
| 공휴일 지정·근무일 보정 | PUT `/wards/me/holidays/{YYYY-MM-DD}` | 수간호사 |
| 공휴일 보정 삭제·기본값 복원 | DELETE `/wards/me/holidays/{YYYY-MM-DD}` | 수간호사 |
| 간호사 등록 | POST `/wards/me/nurses` | 수간호사 |
| 목록·상세 | GET `/wards/me/nurses`, GET `/wards/me/nurses/{nurseId}` | 병동 구성원, 응답 필드 차등 |
| 간호사 정보 변경 | PATCH `/wards/me/nurses/{nurseId}` | 수간호사 |
| 퇴사 | POST `/wards/me/nurses/{nurseId}/retire` | 수간호사 |
| 차지 목록 | GET `/wards/me/charges` | 수간호사 |
| 차지 지정·해제 | PUT `/wards/me/nurses/{nurseId}/charge` | 수간호사 |
| 희망 오프 마감 조회 | GET `/wards/me/off-request-deadlines/{YYYY-MM}` | 병동 구성원 |
| 희망 오프 마감 설정 | PUT `/wards/me/off-request-deadlines/{YYYY-MM}` | 수간호사 |

`/docs`에서 전체 요청 스키마를 확인할 수 있다. 이 PR은 기존 `feat/request` 기반 기능과
`feat/rule`의 파라미터 검증 수정을 재사용한다. main이 초기 구성만 포함하므로 인증·병동·근무표·신청
기반 코드도 main 대비 변경에 포함된다. 프론트엔드는 포함하지 않는다.

## 파라미터 기반 규칙

규칙 정의·기본값·허용 파라미터는 `app/domain/rule_catalog.py`에 있다.
DB의 WardRule을 `Rules.settings[code] = RuleSetting(enabled, severity, parameters)`로 읽어
생성기와 검증기에 전달한다. 배정 가능 여부와 최종 위반 검사는 `rule_engine.assignment_violations`를
공유하며 이전 달 근무와 미래 고정 셀을 포함해 검사한다.

| 코드 | parameters | 기본 심각도 |
| --- | --- | --- |
| COVERAGE | `{"D": 1, "E": 1, "N": 1}` (병동 생성값) | HARD |
| MAX_CONSECUTIVE_NIGHTS | `{"max": 3}` | HARD |
| MAX_CONSECUTIVE_WORK | `{"max": 5}` | HARD |
| CHARGE_DAY_ONLY | `{"allowedDuties": ["D"]}` | HARD |
| NEW_NIGHT_ALONE | `{"minExperienced": 1}` | HARD |
| NIGHT_TO_DAY | `{}` | HARD |
| PREGNANT_NIGHT, OUT_OF_AFFILIATION | `{}` | HARD, 완화·비활성화 불가 |
| OFF_TARGET, WISH_OFF_IGNORED, WISH_DUTY_IGNORED, PRECEPTOR_MISMATCH | `{}` | SOFT |

`max`, `minExperienced`는 1~31 정수이며 boolean은 거절한다. 필요 인원은 D/E/N 모두 필수,
각각 0 이상 정수이고 합계가 1 이상이어야 한다. allowedDuties는 중복 없는 D/E/N 목록이다.
미지원 파라미터는 `400 INVALID_RULE_VALUE`. 기존 DB의 차지·신입 규칙 `{}`는 기본값으로 읽는다.
파라미터 객체에서 생략한 키는 기본값으로 채우며, COVERAGE는 세 키를 모두 보내야 한다.

```json
{
  "parameters": {"max": 2},
  "severity": "HARD",
  "enabled": true,
  "version": 1,
  "reason": "병동 기준 변경"
}
```

응답은 `{ "rule": <갱신한 규칙>, "violationSummary": [...] }`이며 새 설정을 즉시 검증에 사용한다.
version을 보내면 오래된 버전은 `409 VERSION_CONFLICT`. 비활성 규칙은 위반 목록에서 제외한다.
SOFT는 위반을 보고하지만 생성 후보를 차단하지 않는다. COVERAGE 값은 비활성·SOFT 상태에서도
생성기의 인원 목표로 사용된다. 그리디 편성은 최적해를 보장하지 않으며 최종 HARD 위반이 남으면
`NO_SOLUTION`으로 반환하고 초안을 덮어쓰지 않는다.

## 공휴일

PUT 본문 예: `{"isHoliday": true, "name": "병원 휴일", "reason": "운영 일정"}`.
`isHoliday: false`는 해당 날짜를 공휴일에서 제외한다. DELETE는 병동 보정만 지운다.
원래 기본 공휴일이었다면 다시 기본 공휴일로 표시되며, 사용자 추가 휴일이면 목록에서 사라진다.
보정이 없으면 `404 HOLIDAY_OVERRIDE_NOT_FOUND`, 확정·보관 월은 `422 DATE_OUT_OF_SCOPE`.
변경은 월 OFF 자동 목표와 근무표 검증·통계의 다음 조회에 반영된다.
기존 자동 OFF 목표식(기본·보정 공휴일 수 + 1)을 유지한다. 주말·음력·대체공휴일 자동 계산은
포함하지 않으며 필요한 날짜는 병동에서 보정해야 한다.

## 간호사와 차지

간호사 등록 예:

```json
{
  "name": "간호사", "dutyRole": "GENERAL", "status": "ACTIVE",
  "joinedAt": "2025-01-01", "careerMonths": 12, "skillLevel": 3,
  "affiliationStart": "2025-01-01", "affiliationEnd": null, "preceptorOf": []
}
```

PATCH는 변경할 필드와 선택적 `version`을 받는다. role 변경은 이 API에서 거절한다.
차지 PUT은 `{"isCharge": true, "version": 1}`이며 version은 필수다.
`true`는 dutyRole을 CHARGE로, `false`는 현재 CHARGE인 경우 GENERAL로 바꾼다.
이미 다른 역할이면 해제 요청이 그 역할을 바꾸지 않는다. PRECEPTOR/NEW 등으로 변경하려면
기존 간호사 PATCH를 사용한다. 차지는 권한(role)이 아니라 배정 역할(dutyRole)이다.

응답은 기존 간호사 수정과 동일한 `{ "nurse": ..., "violations": [...], "warnings": [...] }`.
변경 후 근무표를 재검증하되 기존 배정 셀은 자동 변경하지 않는다.
퇴사자는 변경 불가, 다른 병동 간호사는 404, 일반 간호사의 변경 요청은 403이다.

## 월별 오프 마감

오프 마감은 **해당 월의 PREFERRED_OFF 신규 접수**에 적용한다.
연차·희망 근무 신청, 기존 신청 승인·반려·취소에는 영향을 주지 않는다.
기존 확정 월 차단, 신청 한도·중복·과거 날짜 검사는 그대로 적용한다.
설정이 없으면 접수 가능하며 version은 0이다.

```json
{
  "closesAt": "2026-10-25T18:00:00+09:00",
  "manuallyClosed": false,
  "version": 0
}
```

- closesAt은 시간대가 있는 ISO 8601 시각 또는 null이다. UTC로 저장·반환한다.
- 현재 시각이 closesAt **이상**이면 마감한다.
- manuallyClosed=true이면 시각과 관계없이 즉시 마감한다.
- 재개는 manuallyClosed=false와 미래 시각 또는 null을 보낸다.
- PUT은 전체 설정 교체다. 생략한 manuallyClosed는 false이며 closesAt·version은 필수다.
- GET/PUT 응답: `{yearMonth, closesAt, manuallyClosed, isClosed, version}`.
- 마감 뒤 접수는 `409 OFF_REQUEST_CLOSED`. 오래된 설정 변경은 `409 VERSION_CONFLICT`.
- PostgreSQL에서는 설정 변경과 오프 접수가 같은 Ward 행 잠금을 획득하므로 마감과 접수를 직렬화한다.
  월별 설정 생성 전에도 이 잠금이 적용된다. SQLite의 행 잠금 동작은 PostgreSQL과 다르다.

새 `off_request_deadlines` 테이블은 기존 앱의 시작 시 create_all 방식으로 생성된다.
기존 테이블의 컬럼 변경은 없으며 마감 설정이 없는 병동은 종전 접수 동작을 유지한다.
운영 마이그레이션 도입은 기존 프로젝트 후속 작업(#24)이다.

## 검증

`uv run pytest`로 순수 도메인 테스트와 실제 HTTP API 테스트를 실행한다.
`TEST_DATABASE_URL`에 전용 빈 PostgreSQL DB를 지정하면 같은 API 테스트를 해당 DB에서 실행할 수 있다.
테스트 DB에는 업무 데이터를 넣지 않는다.
