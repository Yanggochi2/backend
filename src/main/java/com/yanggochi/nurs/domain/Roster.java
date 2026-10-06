package com.yanggochi.nurs.domain;

import java.time.LocalDate;
import java.time.YearMonth;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 한 달 근무표의 검증·생성 입력.
 * cells: nurseId -> 날짜 -> 듀티 (없으면 미배정)
 * wishOffs / wishDuties: 승인된 희망 오프 / 희망 근무
 * before: 이전 달 근무 (읽기 전용). 연속 야간·연속 근무·N→D를 월 경계 너머로 이어 세기 위함
 */
public record Roster(YearMonth month, List<NurseInfo> nurses, Map<Long, Map<LocalDate, Duty>> cells,
                     Rules rules, int offTarget, Map<Long, Set<LocalDate>> wishOffs,
                     Map<Long, Map<LocalDate, Duty>> wishDuties, Map<Long, Map<LocalDate, Duty>> before) {

    /** 이전 달·희망 근무 정보가 없을 때 */
    public Roster(YearMonth month, List<NurseInfo> nurses, Map<Long, Map<LocalDate, Duty>> cells,
                  Rules rules, int offTarget, Map<Long, Set<LocalDate>> wishOffs) {
        this(month, nurses, cells, rules, offTarget, wishOffs, Map.of(), Map.of());
    }

    public Roster withCells(Map<Long, Map<LocalDate, Duty>> newCells) {
        return new Roster(month, nurses, newCells, rules, offTarget, wishOffs, wishDuties, before);
    }

    public List<LocalDate> days() {
        return month.atDay(1).datesUntil(month.atEndOfMonth().plusDays(1)).toList();
    }

    /** 이번 달 이전 날짜는 before에서 읽는다 */
    public Duty cell(long nurseId, LocalDate d) {
        var src = d.isBefore(month.atDay(1)) ? before : cells;
        return src.getOrDefault(nurseId, Map.of()).get(d);
    }

    public Duty wishDuty(long nurseId, LocalDate d) {
        return wishDuties.getOrDefault(nurseId, Map.of()).get(d);
    }
}
