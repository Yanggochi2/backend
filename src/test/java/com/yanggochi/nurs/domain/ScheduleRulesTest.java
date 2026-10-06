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

    /** 리뷰 #1: 9/29~10/3 연속 야간 5일은 월이 바뀌어도 위반이어야 한다 */
    @Test
    void consecutiveRulesSpanMonthBoundary() {
        Rules rules = Rules.preset(Rules.Preset.STANDARD, 0, 0, 0); // 연속 야간 최대 3, N→D 금지
        var ns = List.of(nurse(1, DutyRole.GENERAL, NurseStatus.ACTIVE), nurse(2, DutyRole.GENERAL, NurseStatus.ACTIVE));
        Map<Long, Map<LocalDate, Duty>> before = Map.of(
                1L, Map.of(START.minusDays(2), Duty.N, START.minusDays(1), Duty.N),
                2L, Map.of(START.minusDays(1), Duty.N));
        var cells = Map.of(1L, Map.of(START, Duty.N, START.plusDays(1), Duty.N, START.plusDays(2), Duty.N),
                2L, Map.of(START, Duty.D));
        var vs = ScheduleValidator.validate(new Roster(OCT, ns, cells, rules, 0, Map.of(), Map.of(), before));
        assertEquals(List.of(START.plusDays(1), START.plusDays(2)), vs.stream()
                .filter(v -> v.ruleId().equals("MAX_CONSECUTIVE_NIGHTS")).map(Violation::date).toList());
        assertTrue(vs.stream().anyMatch(v -> v.ruleId().equals("NIGHT_TO_DAY") && v.nurseId() == 2L && v.date().equals(START)));
        assertTrue(vs.stream().allMatch(v -> v.date() == null || !v.date().isBefore(START)), "이전 달 날짜는 위반으로 보고하지 않음");
    }

    @Test
    void generatorRespectsPreviousMonthStreak() {
        Rules rules = Rules.preset(Rules.Preset.STANDARD, 0, 0, 1);
        var ns = List.of(nurse(1, DutyRole.GENERAL, NurseStatus.ACTIVE), nurse(2, DutyRole.GENERAL, NurseStatus.ACTIVE));
        Map<Long, Map<LocalDate, Duty>> before = Map.of(1L, Map.of(
                START.minusDays(3), Duty.N, START.minusDays(2), Duty.N, START.minusDays(1), Duty.N));
        var cells = ScheduleGenerator.generate(new Roster(OCT, ns, Map.of(), rules, 0, Map.of(), Map.of(), before));
        assertNotEquals(Duty.N, cells.get(1L).get(START), "전달 말 N 3연속이면 1일 N 불가");
    }

    /** 리뷰 #9: 희망 근무는 생성에 반영되고, 미반영 시 소프트 위반 */
    @Test
    void wishDutyIsPreferredAndChecked() {
        Rules rules = Rules.preset(Rules.Preset.STANDARD, 1, 0, 0);
        var ns = List.of(nurse(1, DutyRole.GENERAL, NurseStatus.ACTIVE), nurse(2, DutyRole.GENERAL, NurseStatus.ACTIVE));
        Map<Long, Map<LocalDate, Duty>> wish = Map.of(2L, Map.of(START, Duty.D));
        var cells = ScheduleGenerator.generate(new Roster(OCT, ns, Map.of(), rules, 0, Map.of(), wish, Map.of()));
        assertEquals(Duty.D, cells.get(2L).get(START), "희망한 사람이 먼저 배정");

        var ignored = Map.of(1L, Map.of(START, Duty.D), 2L, Map.of(START, Duty.O));
        var vs = ScheduleValidator.validate(new Roster(OCT, ns, ignored, rules, 0, Map.of(), wish, Map.of()));
        assertTrue(vs.stream().anyMatch(v -> v.ruleId().equals("WISH_DUTY_IGNORED") && v.severity() == Severity.SOFT));
    }
}
