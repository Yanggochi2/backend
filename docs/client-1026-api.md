# 10/26 백엔드 API 변경 계약

기본 경로 `/api/v1`. JSON은 camelCase, 단일 응답은 `{data}`, 목록은 `{data, meta}`, 오류는 기존 `{error}` 형식을 유지한다.

## 1. 수간호사 계정과 병동 생성

수간호사는 `POST /auth/signup` 또는 `POST /auth/login`으로 자신의 계정에 로그인한다. 세션은 HttpOnly 쿠키로 유지하며 상태 변경 요청에는 `CSRF_TOKEN` 쿠키 값을 `X-CSRF-Token` 헤더로 보낸다.
비밀번호는 공백만으로 구성할 수 없고 UTF-8 기준 72바이트 이하여야 한다. 로그인·탈퇴에서도 입력값의 앞뒤 공백을 제거하지 않는다.

`POST /workspaces` — 로그인한 계정을 새 병동의 수간호사로 등록한다.

```json
{
  "hospitalName": "병원명",
  "wardName": "병동명",
  "headName": "수간호사 이름",
  "joinedYear": 2000,
  "careerMonths": 312,
  "affiliationStart": "2026-11-01",
  "requiredStaff": {"D": 7, "E": 5, "N": 4},
  "rulePreset": "STANDARD"
}
```

201 응답은 `ward`, `headNurseId`를 반환한다.

- `GET /wards/me`: 병동 정보.
- 로그인하지 않음: 401 `UNAUTHENTICATED`.
- 수간호사 역할이 아님: 403 `FORBIDDEN`.
- 공유 토큰으로 관리 API 호출 불가. 다른 병동 리소스: 404.
- 일반 POST는 기존 `Idempotency-Key` 지원. 비밀값을 반환하는 `/share-link`는 저장 캐시에서 제외한다.
- 기존 편집 링크 기반 병동은 운영자가 `python -m app.manage link-head-account <wardId> <email>`로 실제 수간호사 계정을 연결한다. 활성 실계정·미소속·교체 가능한 수간호사 1명 조건을 모두 검사한다.

## 2. 간호사 등록·수정

- `POST /wards/me/nurses`: 간호사 등록. 계정 없이 등록한다.
- `PATCH /wards/me/nurses/{id}`: 동일 폼 필드를 부분 수정. `version`으로 충돌 검사를 할 수 있다.

```json
{
  "name": "간호사 이름",
  "dutyRole": "GENERAL",
  "status": "ACTIVE",
  "joinedYear": 2020,
  "careerMonths": 72,
  "skillLevel": 3,
  "affiliationStart": "2026-11-01",
  "affiliationEnd": null,
  "isPreceptor": true,
  "preceptorOf": []
}
```

`joinedYear`는 1900~한국 기준 현재 연도 정수. 문자열/boolean/미래 연도는 400.
`joinedAt`은 더 이상 입력/응답에 없다. 소속 시작·종료는 날짜 그대로다.
수정하지 않은 기존 입사일의 월·일은 DB에서 유지하고, 새 등록/연도 변경 시 내부적으로 1월1일로 정규화한다.
이름+입사 연도가 같은 재직자가 있으면 409 `NURSE_ALREADY_EXISTS`.

`dutyRole`: GENERAL / CHARGE / NEW / NIGHT_ONLY. 구형 PRECEPTOR 입력은 GENERAL + `isPreceptor: true`로 정규화한다.
`preceptorOf`에는 같은 병동의 재직 NEW 간호사 ID만 허용한다. 프리셉터를 지정하지 않은 프로필의 담당 신입 연결은 422.
프리셉터 경력을 임의로 제한하지 않으며 역할과 경력을 분리한다. 요청 본문으로 `role`/`wardId`를 변경할 수 없다.

## 3. 조회·정렬

다음 API 모두 `sort=proficiency`(생략 시 기본) 또는 `sort=name`만 받는다:

- `GET /wards/me/nurses`
- `GET /wards/me/schedules?yearMonth=YYYY-MM`
- `GET /wards/me/schedules/{id}`
- `GET /shared/schedules/{token}`

숙련도순은 수간호사→차지→일반 순서, 각 그룹 경력 개월 내림차순, 같은 경력은 이름/ID로 안정 정렬한다. 프리셉터도 일반 그룹이다.
근무표 응답의 `defaultView: "month"`, `defaultSort: "proficiency"`, `sort`를 이용한다. 월간 표시·화면 초기 정렬은 프론트 변경이 필요하다.

## 4. 자동 생성과 고정 셀

`POST /wards/me/schedules`의 `{yearMonth}`로 초안을 만든다.
`PATCH /wards/me/schedules/{id}/cells`로 수간호사가 종이 신청을 반영한다. AL/ED도 직접 입력·수정 가능하며 소속 기간 밖은 거절한다.

```json
{
  "baseVersion": 1,
  "changes": [{"nurseId": "UUID", "date": "2026-11-10", "dutyCode": "O"}]
}
```

`POST /wards/me/schedules/{id}/generations`:

```json
{
  "fixedCells": [{"nurseId": "UUID", "date": "2026-11-10"}],
  "maxSeconds": 30
}
```

종이 달력의 지정 오프 등은 셀 입력 후 `fixedCells`에 포함한다. AL·ED는 목록에 없어도 보존된다.
생성 결과 `SUCCEEDED`만 자동 적용된다. `NO_SOLUTION`은 충돌과 부분 결과를 제공하며 기존 셀/버전을 유지한다.
편집 잠금을 썼다면 `X-Schedule-Lock-Token`도 전달한다.

`GET /wards/me/off-targets/{YYYY-MM}`: `autoCount`는 주말∪등록 공휴일 + 1. 수동 보정은 `targetCount`, 자동 복귀는 `null`.
차지/수간호사는 주5일 달력 기준, 일반 간호사는 해당 O 목표가 기본 하드 제약이다. 통계의 개인별 `offTarget`은 근무 형태와 소속 기간을 반영한다.
AL·ED는 O로 세지 않는다. 중도 소속은 월 목표에 소속 일수 비율을 적용하고 반올림한다. 휴직자는 전체 O.

## 5. 공유 링크

확정은 기존 `POST /wards/me/schedules/{id}/confirm`으로 진행한다. 하드 위반 0과 소프트 위반 확인이 필요하다. 과거 온라인 신청 대기 기록은 확정을 막지 않는다.

- `POST /wards/me/schedules/{id}/share-link`: 확정/보관본 링크 발급 또는 교체. 201 `{shareToken, sharePath, scheduleId}`.
- `sharePath`: `/shared/<token>` 프론트엔드 상대 경로. 화면에서 아래 읽기 API를 호출한다.
- `GET /shared/schedules/{token}`: 로그인/관리 토큰 없이 조회. 월·상태·버전·간호사 이름/직급·셀만 반환.
- `DELETE /wards/me/schedules/{id}/share-link`: 204, 공유 폐기.
- 확정 취소도 기존 링크를 폐기하며 재확정해도 옛 링크가 살아나지 않는다.
- 공개 응답에 경력, 입사 연도, 임신/휴직 상태, 담당 신입, 사유, 잠금, 통계, 편집 권한을 넣지 않는다. AL/ED는 공개 셀에서 O로 표시한다.
- 공개 링크의 PATCH/POST 편집 기능은 없다. 모든 응답은 `Cache-Control: no-store`, `Referrer-Policy: no-referrer`.

## 6. 제거된 화면·API와 프론트 작업

인증 `/auth/*`, `/me`, 가입/초대/권한 이관, 신청 `/wards/me/requests*`, 오프 마감, 개인 알림은 제거되어 404다.

프론트에서 수행할 작업:

1. 수간호사 회원가입·로그인과 세션 확인 화면을 유지하고 일반 간호사 가입·신청·승인 화면을 제거한다.
2. 수간호사 관리 화면과 `/shared/[token]` 조회 화면, 공유 링크 교체/복사 UI를 만든다.
3. 등록·수정 폼을 공유하고 `joinedAt`을 `joinedYear`, 프리셉터를 별도 체크/지정 필드로 바꾼다.
4. 근무표 월간 기본 화면, 숙련도/이름 두 정렬 옵션을 반영한다.
5. 공개 화면은 읽기 전용으로 구성한다. 생성 실패·공유 폐기·관리 토큰 교체 상태를 구분해 표시한다.

이번 작업 범위는 백엔드이며 기존 프론트엔드 main은 아직 이 계약으로 연동되지 않았다.

## 7. 간호사 삭제·엑셀 일괄 등록·잔여 OFF

- `DELETE /wards/me/nurses/{nurseId}`: 수간호사 전용, 204. 목록에서는 즉시 삭제되지만 과거 근무표 보존을 위해 내부적으로 퇴사 처리한다.
- `GET /wards/me/nurses/import-template.xlsx`: 수간호사 전용 간호사 등록 양식 다운로드.
- `POST /wards/me/nurses/import.xlsx`: 양식 파일을 `file`로 업로드한다. 성공 시 201 `{createdCount, nurses}`. 한 행이라도 잘못되면 `422 INVALID_NURSE_EXCEL`로 전체 등록을 취소하고 `details.errors`에 행·필드를 반환한다.
- 근무표 `statistics`의 각 간호사 항목에 `remainingOff`가 추가된다. 계산식은 `max(offTarget - O, 0)`이며 AL·ED는 O로 세지 않는다.
