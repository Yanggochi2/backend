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
