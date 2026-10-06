package com.yanggochi.nurs.domain;

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static com.yanggochi.nurs.domain.Severity.HARD;
import static com.yanggochi.nurs.domain.Severity.SOFT;

/** SCH-06. 프레임워크에 의존하지 않는 순수 규칙 검증. */
public final class ScheduleValidator {
    /** 이전 달을 거슬러 볼 일수. 연속 규칙 최대값(31)을 덮는다 */
    private static final int LOOKBACK_DAYS = 31;

    private ScheduleValidator() {
    }

    public static List<Violation> validate(Roster r) {
        List<Violation> out = new ArrayList<>();
        List<LocalDate> days = r.days();
        Rules rules = r.rules();

        for (NurseInfo n : r.nurses()) {
            int nights = 0, work = 0, offs = 0;
            Duty prev = null;
            Set<LocalDate> wish = r.wishOffs().getOrDefault(n.id(), Set.of());
            // 이전 달 말의 연속 근무를 먼저 세어 둔다. 위반은 이번 달 날짜만 보고한다
            for (LocalDate d = days.get(0).minusDays(LOOKBACK_DAYS); d.isBefore(days.get(0)); d = d.plusDays(1)) {
                Duty x = r.cell(n.id(), d);
                nights = x == Duty.N ? nights + 1 : 0;
                work = x != null && x.isWork() ? work + 1 : 0;
                prev = x;
            }
            for (LocalDate d : days) {
                Duty x = r.cell(n.id(), d);
                if (x != null && !n.affiliated(d))
                    out.add(new Violation(HARD, "OUT_OF_AFFILIATION", n.id(), d, x, n.name() + " 소속 기간 밖 배정"));
                if (n.dutyRole() == DutyRole.CHARGE && (x == Duty.E || x == Duty.N))
                    out.add(new Violation(HARD, "CHARGE_DAY_ONLY", n.id(), d, x, "차지는 D만 배정 가능"));
                if (n.status() == NurseStatus.PREGNANT && x == Duty.N)
                    out.add(new Violation(HARD, "PREGNANT_NIGHT", n.id(), d, x, "임신 상태 N 배정 불가"));

                nights = x == Duty.N ? nights + 1 : 0;
                if (nights > rules.maxConsecutiveNights())
                    out.add(new Violation(HARD, "MAX_CONSECUTIVE_NIGHTS", n.id(), d, x, "연속 야간 " + nights + "일 (최대 " + rules.maxConsecutiveNights() + ")"));
                work = x != null && x.isWork() ? work + 1 : 0;
                if (work > rules.maxConsecutiveWorkDays())
                    out.add(new Violation(HARD, "MAX_CONSECUTIVE_WORK", n.id(), d, x, "연속 근무 " + work + "일 (최대 " + rules.maxConsecutiveWorkDays() + ")"));
                if (rules.forbidNightToDay() && prev == Duty.N && x == Duty.D)
                    out.add(new Violation(HARD, "NIGHT_TO_DAY", n.id(), d, x, "N 다음날 D 배정 불가"));

                if (x == Duty.O || x == Duty.AL) offs++;
                if (wish.contains(d) && x != Duty.O && x != Duty.AL)
                    out.add(new Violation(SOFT, "WISH_OFF_IGNORED", n.id(), d, x, "희망 오프 미반영"));
                Duty wishDuty = r.wishDuty(n.id(), d);
                if (wishDuty != null && x != wishDuty && x != Duty.AL)
                    out.add(new Violation(SOFT, "WISH_DUTY_IGNORED", n.id(), d, x, "희망 근무 " + wishDuty + " 미반영"));
                for (long p : n.preceptees()) {
                    Duty other = r.cell(p, d);
                    if (x != null && x.isWork() && other != null && other.isWork() && x != other)
                        out.add(new Violation(SOFT, "PRECEPTOR_MISMATCH", n.id(), d, x, "담당 신입과 듀티 불일치"));
                }
                prev = x;
            }
            boolean fullMonth = n.affiliated(days.get(0)) && n.affiliated(days.get(days.size() - 1));
            if (fullMonth && offs < r.offTarget())
                out.add(new Violation(SOFT, "OFF_TARGET", n.id(), null, null, "OFF " + offs + " / 목표 " + r.offTarget()));
        }

        for (LocalDate d : days) {
            Map<Duty, Integer> count = coverage(r, d);
            for (Duty duty : List.of(Duty.D, Duty.E, Duty.N)) {
                int need = rules.required(duty), have = count.getOrDefault(duty, 0);
                if (have < need)
                    out.add(new Violation(HARD, "COVERAGE", null, d, duty, duty + " 필요 " + need + "명 / 배정 " + have + "명"));
            }
            boolean veteranOnNight = r.nurses().stream()
                    .anyMatch(n -> n.dutyRole() != DutyRole.NEW && r.cell(n.id(), d) == Duty.N && n.affiliated(d));
            if (!veteranOnNight)
                r.nurses().stream()
                        .filter(n -> n.dutyRole() == DutyRole.NEW && r.cell(n.id(), d) == Duty.N)
                        .forEach(n -> out.add(new Violation(HARD, "NEW_NIGHT_ALONE", n.id(), d, Duty.N, "신입 N 단독 배치")));
        }
        return out;
    }

    /** SCH-05. 소속 기간 밖·미배정은 세지 않는다. */
    public static Map<Duty, Integer> coverage(Roster r, LocalDate d) {
        Map<Duty, Integer> m = new java.util.EnumMap<>(Duty.class);
        for (NurseInfo n : r.nurses()) {
            Duty x = r.cell(n.id(), d);
            if (x != null && n.affiliated(d)) m.merge(x, 1, Integer::sum);
        }
        return m;
    }

    public static boolean hasHard(List<Violation> vs) {
        return vs.stream().anyMatch(v -> v.severity() == HARD);
    }
}
