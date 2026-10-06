package com.yanggochi.nurs.domain;

import java.time.LocalDate;
import java.util.Set;

/** 규칙 검증·생성에 필요한 간호사 정보만 담은 순수 모델 */
public record NurseInfo(long id, String name, DutyRole dutyRole, NurseStatus status,
                        LocalDate affiliationStart, LocalDate affiliationEnd, Set<Long> preceptees) {

    public boolean affiliated(LocalDate d) {
        return !d.isBefore(affiliationStart) && (affiliationEnd == null || !d.isAfter(affiliationEnd));
    }
}
