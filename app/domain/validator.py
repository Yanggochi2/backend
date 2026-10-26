"""SCH-06. 프레임워크에 의존하지 않는 순수 규칙 검증."""
from collections import Counter
from dataclasses import replace
from datetime import date, timedelta

from app.domain.model import (NIGHT_ONLY_MAX_NIGHTS, SENIOR_ROLES, Duty, DutyRole, NurseStatus, Roster, Severity,
                              Violation)
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
            out.extend(_patterns(r, n, d, x))

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

        tolerance = rules.parameter("OFF_TARGET", "maxDeviation")
        target = off_target(r, n)
        if n.affiliated(days[0]) and n.affiliated(days[-1]) and abs(offs - target) > tolerance:
            direction = "과다" if offs > target else "부족"
            out.append(Violation(
                SOFT, "OFF_TARGET", n.id, None, None,
                f"OFF {direction}: {offs}일 / 목표 {target}일 (허용 편차 ±{tolerance}일)",
            ))
        nights = sum(1 for d in days if r.cell(n.id, d) == Duty.N)
        limit = NIGHT_ONLY_MAX_NIGHTS if n.duty_role == DutyRole.NIGHT_ONLY else rules.parameter("MAX_MONTHLY_NIGHTS", "max")
        if nights > limit:
            out.append(Violation(SOFT, "MAX_MONTHLY_NIGHTS", n.id, None, None, f"월 야간 {nights}회 (최대 {limit})"))
        out.extend(_weekly_rest(r, n, days))

    for d in days:
        count = coverage(r, d)
        for duty in (Duty.D, Duty.E, Duty.N):
            need, have = rules.required(duty), count.get(duty, 0)
            if have != need:
                direction = "부족" if have < need else "초과"
                out.append(Violation(
                    HARD, "COVERAGE", None, d, duty,
                    f"{duty} 인원 {direction}: 필요 {need}명 / 배정 {have}명",
                ))
        for code, roles, label in (("CHARGE_EVERY_SHIFT", {DutyRole.CHARGE}, "차지"),
                                   ("SENIOR_EVERY_SHIFT", SENIOR_ROLES, "고연차(차지·프리셉터)")):
            if not has_role(r, roles):
                continue
            for duty in (Duty.D, Duty.E, Duty.N):
                if rules.required(duty) > 0 and not any(
                        n.duty_role in roles and n.affiliated(d) and r.cell(n.id, d) == duty for n in r.nurses):
                    out.append(Violation(HARD, code, None, d, duty, f"{duty} 근무에 {label} 없음"))
    return [replace(v, severity=rules.severity_for(v.rule_id)) for v in out if rules.enabled(v.rule_id)]


OFFISH = (Duty.O, Duty.AL)


def off_target(r: Roster, n) -> int:
    """간호사의 월 OFF 목표. 나이트 전담은 나이트(=근무일)가 월 15회를 넘지 않도록 그 달 일수 - 15 이상"""
    if n.duty_role == DutyRole.NIGHT_ONLY and r.off_target > 0:
        return max(r.off_target, len(r.days()) - NIGHT_ONLY_MAX_NIGHTS)
    return r.off_target


def has_role(r: Roster, roles) -> bool:
    """근무마다 차지·고연차 규칙은 병동에 그 역할이 한 명이라도 있을 때만 적용한다"""
    return any(n.duty_role in roles and n.status != NurseStatus.ON_LEAVE for n in r.nurses)


def _patterns(r: Roster, n, d: date, x: Duty | None) -> list[Violation]:
    """앞뒤 근무를 보는 소프트 규칙. 앞뒤가 미배정이면 판단하지 않는다"""
    out = []
    one = timedelta(days=1)
    p1, p2, nxt = r.cell(n.id, d - one), r.cell(n.id, d - 2 * one), r.cell(n.id, d + one)
    work = x is not None and x.is_work
    if x == Duty.D and p1 in OFFISH and p2 == Duty.N:
        out.append(Violation(SOFT, "NIGHT_OFF_DAY", n.id, d, x, "나이트-오프-데이 (휴식 부족)"))
    if x is not None and p1 is not None and nxt is not None:
        if work and not p1.is_work and not nxt.is_work:
            out.append(Violation(SOFT, "ISOLATED_SHIFT", n.id, d, x, "하루짜리 근무 (앞뒤가 휴무)"))
        elif not work and p1.is_work and nxt.is_work:
            out.append(Violation(SOFT, "ISOLATED_SHIFT", n.id, d, x, "하루짜리 휴무 (앞뒤가 근무)"))
        elif work and p1 != x and nxt != x and (p1.is_work or nxt.is_work):
            out.append(Violation(SOFT, "SHIFT_BLOCK", n.id, d, x, f"{x} 근무가 하루만 (같은 근무 2일 이상 권장)"))
    return out


def _weekly_rest(r: Roster, n, days: list[date]) -> list[Violation]:
    """월~일 한 주 안에 연속 휴무 2일. 그 달 안의 온전한 주, 7일이 모두 정해진 경우만 본다"""
    out = []
    for monday in (d for d in days if d.weekday() == 0 and d + timedelta(days=6) <= days[-1]):
        week = [monday + timedelta(days=i) for i in range(7)]
        xs = [r.cell(n.id, d) for d in week]
        if not all(n.affiliated(d) for d in week) or any(x is None for x in xs):
            continue
        if not any(a in OFFISH and b in OFFISH for a, b in zip(xs, xs[1:])):
            out.append(Violation(SOFT, "WEEKLY_REST", n.id, monday, None, f"{monday:%m/%d} 주 연속 휴무 2일 없음"))
    return out


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
