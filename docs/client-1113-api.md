# 11/13 백엔드 기능 계약

## 간호사·간호조무사 관리

- 간호사 등록·수정·목록 응답에 `staffType`을 제공한다.
  - `NURSE`: 간호사
  - `NURSING_ASSISTANT`: 간호조무사
- `GET /wards/me/nurses?staffType=NURSING_ASSISTANT`로 직종을 필터링한다.
- 간호조무사는 권한 역할과 구분되며 간호사 D/E/N 최소 인원에 포함되지 않는다.
- 간호조무사의 `dutyRole`은 `GENERAL`만 허용한다.

등록 시 `careerMonths`와 `skillLevel`은 생략할 수 있다. 경력은 입사연도에서 가능한 최대 개월로 자동 계산한다.
직접 입력한 경력이 입사연도의 가능한 범위를 벗어나면 `400 VALIDATION_ERROR`를 반환한다. 경력 3개월 이하는
숙련도 1~2만 허용하며, 숙련도를 생략하면 1로 설정한다. 4~6개월은 2, 그보다 길면 3을 초기값으로 사용한다.

엑셀 등록 양식에는 선택 열 `직종`이 추가됐다. 이전 양식처럼 이 열이 없거나 비어 있으면 `NURSE`로 처리한다.
실제 업로드 테스트 파일은 `docs/samples/nurse-import-test.xlsx`에 있다.

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
