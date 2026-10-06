package com.yanggochi.nurs.domain;

import org.junit.jupiter.api.Test;

import java.time.LocalDate;
import java.time.YearMonth;
import java.util.*;

import static org.junit.jupiter.api.Assertions.*;

class ScheduleRulesTest {
    static final YearMonth OCT = YearMonth.of(2026, 10);
    static final LocalDate START = OCT.atDay(1);

    static NurseInfo nurse(long id, DutyRole role, NurseStatus status) {
        return new NurseInfo(id, "n" + id, role, status, START, null, Set.of());
    }

    static Roster roster(List<NurseInfo> ns, Map<Long, Map<LocalDate, Duty>> cells, Rules rules) {
        return new Roster(OCT, ns, cells, rules, 0, Map.of());
    }

    static Set<String> ruleIds(List<Violation> vs) {
        Set<String> s = new HashSet<>();
        vs.forEach(v -> s.add(v.ruleId()));
        return s;
    }

    @Test
    void detectsHardViolations() {
        Rules rules = new Rules(0, 0, 0, 1, 31, true);
        var ns = List.of(nurse(1, DutyRole.CHARGE, NurseStatus.ACTIVE), nurse(2, DutyRole.NEW, NurseStatus.ACTIVE),
                nurse(3, DutyRole.GENERAL, NurseStatus.PREGNANT));
        var cells = Map.of(
                1L, Map.of(START, Duty.N),
                2L, Map.of(START.plusDays(1), Duty.N),
                3L, Map.of(START, Duty.N, START.plusDays(1), Duty.N, START.plusDays(2), Duty.D));
        var ids = ruleIds(ScheduleValidator.validate(roster(ns, cells, rules)));
        assertEquals(Set.of("CHARGE_DAY_ONLY", "PREGNANT_NIGHT", "MAX_CONSECUTIVE_NIGHTS", "NIGHT_TO_DAY"), ids,
                "신입(2)은 같은 날 임신자(3)가 N에 있어 단독이 아님");
        var alone = roster(List.of(ns.get(1)), Map.of(2L, Map.of(START, Duty.N)), rules);
        assertTrue(ruleIds(ScheduleValidator.validate(alone)).contains("NEW_NIGHT_ALONE"));
    }

    @Test
    void generatorSatisfiesHardRulesWhenStaffIsEnough() {
        List<NurseInfo> ns = new ArrayList<>();
        ns.add(nurse(1, DutyRole.CHARGE, NurseStatus.ACTIVE));
        ns.add(nurse(2, DutyRole.NEW, NurseStatus.ACTIVE));
        ns.add(nurse(3, DutyRole.GENERAL, NurseStatus.PREGNANT));
        for (long i = 4; i <= 14; i++) ns.add(nurse(i, DutyRole.GENERAL, NurseStatus.ACTIVE));
        Rules rules = Rules.preset(Rules.Preset.STANDARD, 3, 2, 2);
        Map<Long, Map<LocalDate, Duty>> leave = Map.of(5L, Map.of(START.plusDays(9), Duty.AL));

        var cells = ScheduleGenerator.generate(new Roster(OCT, ns, leave, rules, 0, Map.of()));
        var out = new Roster(OCT, ns, cells, rules, 0, Map.of());

        assertEquals(Duty.AL, out.cell(5, START.plusDays(9)), "연차는 유지");
        var hard = ScheduleValidator.validate(out).stream().filter(v -> v.severity() == Severity.HARD).toList();
        assertEquals(List.of(), hard);
    }

    @Test
    void reportsCoverageShortageWhenStaffIsShort() {
        Rules rules = Rules.preset(Rules.Preset.STANDARD, 3, 3, 3);
        var ns = List.of(nurse(1, DutyRole.GENERAL, NurseStatus.ACTIVE));
        var cells = ScheduleGenerator.generate(roster(ns, Map.of(), rules));
        assertTrue(ruleIds(ScheduleValidator.validate(roster(ns, cells, rules))).contains("COVERAGE"));
    }

    @Test
    void generatorKeepsFixedCellsAndCountsThemTowardCoverage() {
        List<NurseInfo> ns = new ArrayList<>();
        for (long i = 1; i <= 12; i++) ns.add(nurse(i, DutyRole.GENERAL, NurseStatus.ACTIVE));
        Rules rules = Rules.preset(Rules.Preset.STANDARD, 2, 2, 2);
        var cells = ScheduleGenerator.generate(new Roster(OCT, ns, Map.of(1L, Map.of(START, Duty.N)), rules, 0, Map.of()));
        assertEquals(Duty.N, cells.get(1L).get(START));
        long nights = ns.stream().filter(n -> cells.get(n.id()).get(START) == Duty.N).count();
        assertEquals(2, nights, "고정 셀 포함 필요 인원만큼만 배정");
    }
}
