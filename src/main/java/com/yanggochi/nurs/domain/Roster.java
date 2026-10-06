package com.yanggochi.nurs.domain;

import java.time.LocalDate;
import java.time.YearMonth;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 한 달 근무표의 검증·생성 입력.
 * cells: nurseId -> 날짜 -> 듀티 (없으면 미배정)
 * wishOffs: 승인된 희망 오프 (nurseId -> 날짜들)
 */
public record Roster(YearMonth month, List<NurseInfo> nurses, Map<Long, Map<LocalDate, Duty>> cells,
                     Rules rules, int offTarget, Map<Long, Set<LocalDate>> wishOffs) {

    public List<LocalDate> days() {
        return month.atDay(1).datesUntil(month.atEndOfMonth().plusDays(1)).toList();
    }

    public Duty cell(long nurseId, LocalDate d) {
        return cells.getOrDefault(nurseId, Map.of()).get(d);
    }
}
