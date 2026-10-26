"""SCH-06. 프레임워크에 의존하지 않는 순수 규칙 검증."""
from collections import Counter
from dataclasses import replace
from datetime import date, timedelta

from app.domain.model import (NIGHT_ONLY_MAX_NIGHTS, SENIOR_ROLES, Duty, DutyRole, NurseStatus, Roster, Severity,
                              Violation)

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
            if n.duty_role == DutyRole.NIGHT_ONLY and x in (Duty.D, Duty.E):
                out.append(Violation(HARD, "NIGHT_ONLY_SHIFT", n.id, d, x, "나이트 전담은 N만 배정 가능"))
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
            if rules.forbid_night_to_day and (prev, x) in REVERSE:
                out.append(Violation(HARD, "NIGHT_TO_DAY", n.id, d, x, f"역순환 {prev}→{x} (근무 사이 11시간 휴식 부족)"))
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
            prev = x

        if n.affiliated(days[0]) and n.affiliated(days[-1]) and offs < off_target(r, n):
            out.append(Violation(SOFT, "OFF_TARGET", n.id, None, None, f"OFF {offs} / 목표 {off_target(r, n)}"))
        month_nights = sum(1 for d in days if r.cell(n.id, d) == Duty.N)
        limit = NIGHT_ONLY_MAX_NIGHTS if n.duty_role == DutyRole.NIGHT_ONLY else rules.max_monthly_nights
        if month_nights > limit:
            out.append(Violation(SOFT, "MAX_MONTHLY_NIGHTS", n.id, None, None, f"월 야간 {month_nights}회 (최대 {limit})"))
        out.extend(_weekly_rest(r, n, days))

    for d in days:
        count = coverage(r, d)
        for duty in (Duty.D, Duty.E, Duty.N):
            need, have = rules.required(duty), count.get(duty, 0)
            if have < need:
                out.append(Violation(HARD, "COVERAGE", None, d, duty, f"{duty} 필요 {need}명 / 배정 {have}명"))
        for code, roles, label in (("CHARGE_EVERY_SHIFT", {DutyRole.CHARGE}, "차지"),
                                   ("SENIOR_EVERY_SHIFT", SENIOR_ROLES, "고연차(차지·프리셉터)")):
            if not has_role(r, roles):
                continue
            for duty in (Duty.D, Duty.E, Duty.N):
                if rules.required(duty) > 0 and not any(
                        n.duty_role in roles and n.affiliated(d) and r.cell(n.id, d) == duty for n in r.nurses):
                    out.append(Violation(HARD, code, None, d, duty, f"{duty} 근무에 {label} 없음"))
        veteran_on_night = any(n.duty_role != DutyRole.NEW and r.cell(n.id, d) == Duty.N and n.affiliated(d)
                               for n in r.nurses)
        if not veteran_on_night:
            for n in r.nurses:
                if n.duty_role == DutyRole.NEW and r.cell(n.id, d) == Duty.N:
                    out.append(Violation(HARD, "NEW_NIGHT_ALONE", n.id, d, Duty.N, "신입 N 단독 배치"))
    # RULE-01: 병동 설정으로 꺼진 규칙(기본 꺼짐 포함)은 빼고, 심각도를 바꾼 규칙은 덮어쓴다
    return [replace(v, severity=sev) for v in out if (sev := rules.mode(v.rule_id)) is not None]


def off_target(r: Roster, n) -> int:
    """간호사의 월 OFF 목표. 나이트 전담은 나이트(=근무일)가 월 15회를 넘지 않도록 그 달 일수 - 15 이상"""
    if n.duty_role == DutyRole.NIGHT_ONLY and r.off_target > 0:
        return max(r.off_target, len(r.days()) - NIGHT_ONLY_MAX_NIGHTS)
    return r.off_target


def has_role(r: Roster, roles) -> bool:
    """근무마다 차지·고연차 규칙은 병동에 그 역할이 한 명이라도 있을 때만 적용한다"""
    return any(n.duty_role in roles and n.status != NurseStatus.ON_LEAVE for n in r.nurses)


REVERSE = {(Duty.E, Duty.D), (Duty.N, Duty.D), (Duty.N, Duty.E)}  # 역순환: 근무 사이 휴식이 11시간 미만
OFFISH = (Duty.O, Duty.AL)


def _patterns(r: Roster, n, d: date, x: Duty | None) -> list[Violation]:
    """d의 앞뒤 근무를 보는 규칙. 이전 달 말 근무도 이어서 본다"""
    out = []
    one = timedelta(days=1)
    p1, p2, p3 = (r.cell(n.id, d - k * one) for k in (1, 2, 3))
    nxt = r.cell(n.id, d + one)
    work = x is not None and x.is_work
    # 야간 2일 이상 연속 후 48시간(OFF 2일) 휴식
    if work and ((x != Duty.N and p1 == Duty.N and p2 == Duty.N)
                 or (p1 != Duty.N and p2 == Duty.N and p3 == Duty.N)):
        out.append(Violation(HARD, "NIGHT_REST", n.id, d, x, "야간 2일 연속 후 OFF 2일 필요"))
    # 하루짜리 시프트 (앞뒤가 다른 근무, 퐁당퐁당). 앞뒤가 모두 휴무면 고립 배치로 따로 본다
    if (work and p1 is not None and nxt is not None and p1 != x and nxt != x
            and (p1.is_work or nxt.is_work)):
        out.append(Violation(SOFT, "SHIFT_BLOCK", n.id, d, x, f"{x} 근무가 하루만 (같은 근무 2일 이상 권장)"))
    if x == Duty.D and p1 in OFFISH and p2 == Duty.N:
        out.append(Violation(SOFT, "NIGHT_OFF_DAY", n.id, d, x, "나이트-오프-데이 (휴식 부족)"))
    # 고립 배치: 앞뒤가 모두 정해진 날만 본다 (미배정이 끼면 판단하지 않음)
    if x is not None and p1 is not None and nxt is not None:
        if work and not p1.is_work and not nxt.is_work:
            out.append(Violation(SOFT, "ISOLATED_SHIFT", n.id, d, x, "하루짜리 근무 (앞뒤가 휴무)"))
        elif not work and p1.is_work and nxt.is_work:
            out.append(Violation(SOFT, "ISOLATED_SHIFT", n.id, d, x, "하루짜리 휴무 (앞뒤가 근무)"))
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
