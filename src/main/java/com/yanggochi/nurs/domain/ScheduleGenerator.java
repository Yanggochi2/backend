package com.yanggochi.nurs.domain;

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * GEN-01. 날짜 순 그리디 편성. 입력 셀(연차 AL, GEN-05 고정 셀)은 그대로 두고 빈 칸만 채운다.
 * 희망 오프는 후순위, 희망 근무는 해당 듀티에 우선 배정(소프트). 이전 달 말 근무(Roster.before)를 이어서 센다.
 * ponytail: 그리디라 최적해 보장 없음. 해 품질이 부족하면 OR-Tools CP-SAT 등 솔버로 교체.
 */
public final class ScheduleGenerator {

    private ScheduleGenerator() {
    }

    public static Map<Long, Map<LocalDate, Duty>> generate(Roster r) {
        Map<Long, Map<LocalDate, Duty>> cells = new HashMap<>();
        for (NurseInfo n : r.nurses()) {
            cells.put(n.id(), new HashMap<>(r.cells().getOrDefault(n.id(), Map.of())));
        }
        Roster cur = r.withCells(cells);
        Map<Long, Integer> worked = new HashMap<>();

        for (LocalDate d : r.days()) {
            for (Duty duty : List.of(Duty.N, Duty.E, Duty.D)) {
                int need = r.rules().required(duty) - ScheduleValidator.coverage(cur, d).getOrDefault(duty, 0);
                List<NurseInfo> picks = new ArrayList<>(r.nurses().stream()
                        .filter(n -> n.affiliated(d) && cells.get(n.id()).get(d) == null && can(cur, n, duty, d))
                        .sorted(Comparator
                                .comparing((NurseInfo n) -> duty == Duty.N && n.dutyRole() == DutyRole.NEW)
                                .thenComparing(n -> r.wishOffs().getOrDefault(n.id(), Set.of()).contains(d))
                                .thenComparing(n -> r.wishDuty(n.id(), d) != duty)
                                .thenComparing(n -> worked.getOrDefault(n.id(), 0)))
                        .limit(Math.max(0, need))
                        .toList());
                for (NurseInfo n : picks) {
                    cells.get(n.id()).put(d, duty);
                    worked.merge(n.id(), 1, Integer::sum);
                }
            }
            for (NurseInfo n : r.nurses())
                if (n.affiliated(d)) cells.get(n.id()).putIfAbsent(d, Duty.O);
        }
        return cells;
    }

    private static boolean can(Roster r, NurseInfo n, Duty duty, LocalDate d) {
        if (n.dutyRole() == DutyRole.CHARGE && duty != Duty.D) return false;
        if (n.status() == NurseStatus.PREGNANT && duty == Duty.N) return false;
        Rules rules = r.rules();
        if (rules.forbidNightToDay() && duty == Duty.D && r.cell(n.id(), d.minusDays(1)) == Duty.N) return false;
        if (streak(r, n.id(), d, x -> x == Duty.N) >= rules.maxConsecutiveNights() && duty == Duty.N) return false;
        return streak(r, n.id(), d, Duty::isWork) < rules.maxConsecutiveWorkDays();
    }

    private static int streak(Roster r, long id, LocalDate d, java.util.function.Predicate<Duty> p) {
        int k = 0;
        for (LocalDate x = d.minusDays(1); r.cell(id, x) != null && p.test(r.cell(id, x)); x = x.minusDays(1)) k++;
        return k;
    }
}
