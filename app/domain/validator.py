"""SCH-06. 프레임워크에 의존하지 않는 순수 규칙 검증."""
from collections import Counter
from dataclasses import replace
from datetime import date, timedelta

from app.domain.model import Duty, DutyRole, NurseStatus, Roster, Severity, Violation

HARD, SOFT = Severity.HARD, Severity.SOFT
# 이전 달을 거슬러 볼 일수. 연속 규칙 최대값(31)을 덮는다
LOOKBACK_DAYS = 31


def validate(r: Roster) -> list[Violation]:
    out: list[Violation] = []
    days = r.days()
    rules = r.rules

    for n in r.nurses:
        nights = work = offs = 0
        prev: Duty | None = None
        wish = r.wish_offs.get(n.id, set())
        # 이전 달 말의 연속 근무를 먼저 세어 둔다. 위반은 이번 달 날짜만 보고한다
        d = days[0] - timedelta(days=LOOKBACK_DAYS)
        while d < days[0]:
            x = r.cell(n.id, d)
            nights = nights + 1 if x == Duty.N else 0
            work = work + 1 if x and x.is_work else 0
            prev = x
            d += timedelta(days=1)

        for d in days:
            x = r.cell(n.id, d)
            if x is not None and not n.affiliated(d):
                out.append(Violation(HARD, "OUT_OF_AFFILIATION", n.id, d, x, f"{n.name} 소속 기간 밖 배정"))
            if n.duty_role == DutyRole.CHARGE and x in (Duty.E, Duty.N):
                out.append(Violation(HARD, "CHARGE_DAY_ONLY", n.id, d, x, "차지는 D만 배정 가능"))
            if n.status == NurseStatus.PREGNANT and x == Duty.N:
                out.append(Violation(HARD, "PREGNANT_NIGHT", n.id, d, x, "임신 상태 N 배정 불가"))

            nights = nights + 1 if x == Duty.N else 0
            if nights > rules.max_consecutive_nights:
                out.append(Violation(HARD, "MAX_CONSECUTIVE_NIGHTS", n.id, d, x,
                                     f"연속 야간 {nights}일 (최대 {rules.max_consecutive_nights})"))
            work = work + 1 if x and x.is_work else 0
            if work > rules.max_consecutive_work_days:
                out.append(Violation(HARD, "MAX_CONSECUTIVE_WORK", n.id, d, x,
                                     f"연속 근무 {work}일 (최대 {rules.max_consecutive_work_days})"))
            if rules.forbid_night_to_day and prev == Duty.N and x == Duty.D:
                out.append(Violation(HARD, "NIGHT_TO_DAY", n.id, d, x, "N 다음날 D 배정 불가"))

            if x in (Duty.O, Duty.AL):
                offs += 1
            if d in wish and x not in (Duty.O, Duty.AL):
                out.append(Violation(SOFT, "WISH_OFF_IGNORED", n.id, d, x, "희망 오프 미반영"))
            wish_duty = r.wish_duty(n.id, d)
            if wish_duty is not None and x != wish_duty and x != Duty.AL:
                out.append(Violation(SOFT, "WISH_DUTY_IGNORED", n.id, d, x, f"희망 근무 {wish_duty} 미반영"))
            for p in n.preceptees:
                other = r.cell(p, d)
                if x and x.is_work and other and other.is_work and x != other:
                    out.append(Violation(SOFT, "PRECEPTOR_MISMATCH", n.id, d, x, "담당 신입과 듀티 불일치"))
            prev = x

        if n.affiliated(days[0]) and n.affiliated(days[-1]) and offs < r.off_target:
            out.append(Violation(SOFT, "OFF_TARGET", n.id, None, None, f"OFF {offs} / 목표 {r.off_target}"))

    for d in days:
        count = coverage(r, d)
        for duty in (Duty.D, Duty.E, Duty.N):
            need, have = rules.required(duty), count.get(duty, 0)
            if have < need:
                out.append(Violation(HARD, "COVERAGE", None, d, duty, f"{duty} 필요 {need}명 / 배정 {have}명"))
        veteran_on_night = any(n.duty_role != DutyRole.NEW and r.cell(n.id, d) == Duty.N and n.affiliated(d)
                               for n in r.nurses)
        if not veteran_on_night:
            for n in r.nurses:
                if n.duty_role == DutyRole.NEW and r.cell(n.id, d) == Duty.N:
                    out.append(Violation(HARD, "NEW_NIGHT_ALONE", n.id, d, Duty.N, "신입 N 단독 배치"))
    # RULE-01: 병동 설정으로 꺼진 규칙은 빼고, 심각도를 바꾼 규칙은 덮어쓴다
    return [replace(v, severity=sev) for v in out if (sev := rules.severity.get(v.rule_id, v.severity)) is not None]


def coverage(r: Roster, d: date) -> dict[Duty, int]:
    """SCH-05. 소속 기간 밖·미배정은 세지 않는다."""
    c: Counter[Duty] = Counter()
    for n in r.nurses:
        x = r.cell(n.id, d)
        if x is not None and n.affiliated(d):
            c[x] += 1
    return dict(c)


def has_hard(vs: list[Violation]) -> bool:
    return any(v.severity == HARD for v in vs)
