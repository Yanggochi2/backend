# 10/26 백엔드 API 변경 계약

기본 경로 `/api/v1`. JSON은 camelCase, 단일 응답은 `{data}`, 목록은 `{data, meta}`, 오류는 기존 `{error}` 형식을 유지한다.

## 1. 병동 생성과 편집 링크

`POST /workspaces` — 로그인 없이 새 병동을 만들 때만 사용한다. 기존 병동 관리 링크는 운영자 CLI로 발급한다.

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

201 응답: `ward`, `headNurseId`, `managementToken`, `managementPath`.
`managementPath`는 `/manage#token=<비밀값>` 형태의 **프론트엔드 상대 경로 계약**이다. 해당 프론트엔드 화면은 별도 구현해야 한다. 이 API 서버가 HTML 화면을 제공하지 않는다.

관리 API에는 `Authorization: Bearer <managementToken>`을 전송한다. 브라우저 자동 전송 쿠키나 쿼리 토큰은 허용하지 않는다. 프론트는 fragment에서 토큰을 읽고 주소창에서 제거한 뒤 관리 요청 헤더에 넣는다. 다른 도메인 API라면 배포 프록시의 Authorization 전달과 CORS 허용 정책도 설정한다.

- `GET /wards/me`: 병동 정보.
- `POST /wards/me/management-link`: 관리 링크 교체. 응답의 새 토큰으로 헤더를 교체해야 하며 이전 토큰은 즉시 무효.
- 토큰 없음/불일치: 401 `MANAGEMENT_LINK_REQUIRED`.
- 공유 토큰으로 관리 API 호출 불가. 다른 병동 리소스: 404.
- 일반 POST는 기존 `Idempotency-Key` 지원. 비밀값을 반환하는 `/workspaces`, `/management-link`, `/share-link`는 저장 캐시에서 제외하므로 재호출마다 새 링크를 발급한다.

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

1. 로그인/가입/신청/승인 화면과 쿠키 인증 가드를 제거하고 관리 링크 진입·토큰 헤더 전달을 구현한다.
2. `/manage`, `/shared/[token]` 진입 화면과 링크 교체/복사 UI를 만든다.
3. 등록·수정 폼을 공유하고 `joinedAt`을 `joinedYear`, 프리셉터를 별도 체크/지정 필드로 바꾼다.
4. 근무표 월간 기본 화면, 숙련도/이름 두 정렬 옵션을 반영한다.
5. 공개 화면은 읽기 전용으로 구성한다. 생성 실패·공유 폐기·관리 토큰 교체 상태를 구분해 표시한다.

이번 작업 범위는 백엔드이며 기존 프론트엔드 main은 아직 이 계약으로 연동되지 않았다.
