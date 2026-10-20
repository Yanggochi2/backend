"""SCH-06. 프레임워크에 의존하지 않는 순수 규칙 검증."""
from collections import Counter
from dataclasses import replace
from datetime import date

from app.domain.model import Duty, Roster, Severity, Violation
from app.domain.rule_engine import assignment_violations

HARD, SOFT = Severity.HARD, Severity.SOFT


def validate(r: Roster) -> list[Violation]:
    out: list[Violation] = []
    days = r.days()
    rules = r.rules

    for n in r.nurses:
        offs = 0
        wish = r.wish_offs.get(n.id, set())
        for d in days:
            x = r.cell(n.id, d)
            out.extend(assignment_violations(r, n, d))

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

        if n.affiliated(days[0]) and n.affiliated(days[-1]) and offs < r.off_target:
            out.append(Violation(SOFT, "OFF_TARGET", n.id, None, None, f"OFF {offs} / 목표 {r.off_target}"))

    for d in days:
        count = coverage(r, d)
        for duty in (Duty.D, Duty.E, Duty.N):
            need, have = rules.required(duty), count.get(duty, 0)
            if have < need:
                out.append(Violation(HARD, "COVERAGE", None, d, duty, f"{duty} 필요 {need}명 / 배정 {have}명"))
    return [replace(v, severity=rules.severity_for(v.rule_id)) for v in out if rules.enabled(v.rule_id)]


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
