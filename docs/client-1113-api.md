# 11/13 백엔드 기능 계약

## 간호사·간호조무사 관리

- 간호사 등록·수정·목록 응답에 `staffType`을 제공한다.
  - `NURSE`: 간호사
  - `NURSING_ASSISTANT`: 간호조무사
- `GET /wards/me/nurses?staffType=NURSING_ASSISTANT`로 직종을 필터링한다.
- 간호조무사는 권한 역할과 구분되며 간호사 D/E/N 최소 인원에 포함되지 않는다.
- 간호조무사의 `dutyRole`은 `GENERAL`만 허용한다.

단건 추가는 최신 프론트 폼에 맞춰 간호사의 `dutyRole`, `isPreceptor`, `preceptorOf`를 받는다.
간호조무사는 이 입력을 생략할 수 있으며 서버가 `GENERAL`, 프리셉터 아님으로 처리한다. 프론트 호환을 위해
`skillLevel`을 받을 수 있지만 신규 등록 숙련도에는 사용하지 않는다. 초기 숙련도는 전체 경력 3개월 이하이면 1,
4~6개월이면 2, 그보다 경력이 길어도 병동 소속 3개월 이내이면 2, 나머지는 3으로 서버가 자동 설정한다.

엑셀 등록 양식은 단건 추가 폼처럼 총 경력을 `년`과 `개월`로 나눠 입력한다. 간호사 행에는 직급과
프리셉터 여부를 입력하고, 간호조무사 행은 두 칸을 비운다. 숙련도·소속 종료일·담당 신입은 받지 않는다.
실제 업로드 테스트 파일은
`docs/samples/nurse-import-test.xlsx`에 있다.

## 근무표

- `PATCH /wards/me/schedules/{scheduleId}/forced-offs`

```json
{
  "baseVersion": 3,
  "reason": "인력 운영 조정",
  "cells": [{"nurseId": "UUID", "date": "2026-12-03"}]
}
```

수간호사만 호출할 수 있고 초안 셀을 `O`로 지정한다. 커버리지 등 발생한 위반은 응답에 그대로 포함하며
감사 로그에 `FORCED_OFF_ASSIGNED`로 기록한다.

확정된 전월에 사용하지 못한 O는 기존 구현대로 다음 달 개인별 `offCarry`와 `offTarget`에 반영한다.

## 공유 링크 검색

`GET /shared/schedules/{token}?q=김간호`는 이름에 검색어가 포함된 인원과 해당 인원의 셀만 반환한다.
검색 결과도 읽기 전용이며 공개 응답에는 민감한 인사 정보가 포함되지 않는다.

## 대시보드

`GET /wards/me/dashboard?yearMonth=2026-12`는 수간호사 전용이다. 그래프에서 바로 사용할 수 있도록 다음을 반환한다.

- 직종별·재직 상태별 인원
- 듀티별 총 배정 수
- 일자·듀티별 필요/실제 인원과 충족 상태
- 전체 OFF 목표·배정·잔여·이월 수
- 개인별 D/E/N/O/AL/ED와 잔여·이월 OFF

해당 월 근무표가 없어도 인력 현황과 빈 통계 구조를 반환한다.

## 자동 생성 전 인원 부족 차단 (11/23 미팅)

`POST /wards/me/schedules/{scheduleId}/generations`는 지금 인원으로 하루 필요 인원(D/E/N)을 한 달 내내
채울 수 없으면 생성하지 않고 `422 STAFFING_SHORTAGE`를 돌려준다. 필요 인원을 낮추는 대신 모자란 인원을 알린다.

```json
{"error": {"code": "STAFFING_SHORTAGE",
  "message": "지금 인원으로는 하루 필요 인원을 채울 수 없습니다. 간호사 약 2명이 더 필요합니다",
  "details": {"shortDays": 32, "nursesNeeded": 2, "supply": 420, "demand": 452, "surplus": -32,
              "current": {"D": 7, "E": 5, "N": 4}, "recommended": {"D": 5, "E": 5, "N": 3}}}}
```

- `supply`: 교대 간호사(차지·수간호사·간호조무사·휴직자 제외)의 한 달 근무 가능일 합 = 소속 일수 − O 목표 − AL/ED 칸
- `demand`: 하루 필요 인원 × 일수 − 평일 D 고정 인원(차지 등)이 메우는 D
- `shortDays` = `demand − supply`, `nursesNeeded` = `shortDays ÷ (일수 − O 목표)` 올림
- 판단식은 `GET /wards/me/schedules/{id}/staffing-recommendation`과 같다. 생성 전에 미리 보여 줄 때 이 API를 쓴다.

수간호사가 경고를 보고도 만들겠다고 하면 요청 본문에 `"allowShortage": true`를 넣어 다시 보낸다. 이때는 기존처럼
가장 나은 결과를 만들며, 모자란 날은 하단 인원 행에 미달로 남는다. 규칙 완화 재실행(`/relaxations`)은 확인 없이 진행한다.
골라서 다시 짜기(`fixedCells`)도 같은 API라 인원이 모자란 달에는 `allowShortage`가 필요하다.

### OFF 개수 (기존 규칙 유지)

개인별 OFF 목표가 다른 것은 규칙대로다. 근무표 통계의 `offTarget`, `offCarry`로 이유를 보여 줄 수 있다.

- 일반 간호사: 주말 ∪ 공휴일 날짜 수 + 1 (+ 확정된 지난달에 못 쓴 O `offCarry`)
- 차지·수간호사(평일 D 고정): 주말 ∪ 공휴일 날짜 수 (주5일)
- 중도 소속: 소속 일수에 비례

## 인원이 남을 때 D/E/N 적정 인원 (11/23 미팅)

인원이 남는 달에는 남는 근무일을 D에만 몰지 않고 날짜와 D/E/N에 고르게 나눠 그날의 **적정 인원**으로 맞춘다.
O 목표(주말 ∪ 공휴일 + 1)는 그대로 정확히 지킨다. 남는 근무일 합계가 적정 인원 증가분과 같아서 두 조건을 함께 맞춘다.

- 그날 안에서는 병동 설정의 필요 인원 비율(예: 7:5:4)로 나눈다. N은 야간 가능 인원을 넘겨 올리지 않는다.
- 인원이 모자라는 달은 적정 인원 = 필요 인원 (생성 전 `STAFFING_SHORTAGE`로 막힘).
- 예: 데모 병동 28명(남는 근무일 83일)은 날마다 D 8 · E 6 · N 4~5.

커버리지 응답(`GET .../coverage`, 셀 수정 응답의 `coverage`)이 바뀌었다.

| 필드 | 뜻 |
| --- | --- |
| `requiredCount` | 그날 적정 인원 (이전: 병동 설정 필요 인원). 화면 아랫줄 "실제/필요"의 분모 |
| `minimumCount` | 병동 설정의 최소 필요 인원 (새 필드) |
| `status` | `requiredCount` 기준 UNDER/MET/OVER |

확정을 막는 하드 위반(COVERAGE)은 여전히 `minimumCount` 기준이다. 적정 인원보다 적어도 최소 인원 이상이면 확정할 수 있다.
엑셀 내보내기 아랫줄도 `실제/적정 인원`으로 나온다.
