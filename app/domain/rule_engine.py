"""생성 후보와 저장된 근무표가 공유하는 배정 규칙."""
from datetime import date, timedelta

from app.domain.model import Cells, Duty, DutyRole, NurseInfo, NurseStatus, Roster, Violation


REVERSE = {(Duty.E, Duty.D), (Duty.N, Duty.D), (Duty.N, Duty.E)}  # 역순환: 근무 사이 휴식이 11시간 미만


def assignment_violations(r: Roster, n: NurseInfo, d: date) -> list[Violation]:
    rules, x, out = r.rules, r.cell(n.id, d), []

    def add(code, message):
        if rules.enabled(code):
            out.append(Violation(rules.severity_for(code), code, n.id, d, x, message))

    if x is not None and not n.affiliated(d):
        add("OUT_OF_AFFILIATION", f"{n.name} 소속 기간 밖 배정")
    if n.duty_role == DutyRole.CHARGE and x and x.is_work and x not in rules.parameter("CHARGE_DAY_ONLY", "allowedDuties"):
        add("CHARGE_DAY_ONLY", "차지 허용 근무 위반")
    if n.status == NurseStatus.PREGNANT and x == Duty.N:
        add("PREGNANT_NIGHT", "임신 상태 N 배정 불가")
    for code, predicate in (("MAX_CONSECUTIVE_NIGHTS", lambda v: v == Duty.N),
                            ("MAX_CONSECUTIVE_WORK", lambda v: v is not None and v.is_work)):
        if not predicate(x):
            continue
        count, previous = 1, d - timedelta(days=1)
        # 최대 파라미터는 31. 위반 판정에 필요한 직전 31일까지 확인한다.
        while count <= 31 and predicate(r.cell(n.id, previous)):
            count += 1
            previous -= timedelta(days=1)
        limit = rules.parameter(code, "max")
        if count > limit:
            add(code, f"연속 근무 {count}일 (최대 {limit})")
    one = timedelta(days=1)
    k = rules.parameter("NIGHT_REST", "nights")
    p = [r.cell(n.id, d - i * one) for i in range(1, k + 2)]  # p[0] = 전날
    p1, p2, p3 = p[0], r.cell(n.id, d - 2 * one), r.cell(n.id, d - 3 * one)
    if (p1, x) in REVERSE:
        add("NIGHT_TO_DAY", f"역순환 {p1}→{x} (근무 사이 휴식 11시간 미만)")
    # 야간 k일 연속 후 48시간(OFF 2일) 휴식
    if x is not None and x.is_work and ((x != Duty.N and all(v == Duty.N for v in p[:k]))
                                        or (p1 != Duty.N and all(v == Duty.N for v in p[1:k + 1]))):
        add("NIGHT_REST", f"야간 {k}일 연속 후 OFF 2일 필요")
    if x is not None and p1 is not None and p2 is not None:
        if x.is_work and p1.is_work and p2.is_work and p2 != p1 != x:
            add("PING_PONG", f"매일 다른 근무 {p2}→{p1}→{x}")
        if p3 is not None and p3.is_work != p2.is_work != p1.is_work != x.is_work:
            add("PING_PONG", f"근무·휴무 교대 {p3} {p2} {p1} {x}")
    if x is not None and r.day_fixed(n) and x.is_work and (r.rest_day(d) or x != Duty.D):
        add("DAY_FIXED", "수간호사·차지는 평일 D, 주말·공휴일 OFF")
    if n.duty_role == DutyRole.NIGHT_ONLY and x in (Duty.D, Duty.E):
        add("NIGHT_ONLY_SHIFT", "나이트 전담은 N만 배정 가능")
    if n.duty_role == DutyRole.NEW and x == Duty.N:
        support = sum(other.duty_role != DutyRole.NEW and other.affiliated(d) and r.cell(other.id, d) == Duty.N
                      for other in r.nurses)
        if support < rules.parameter("NEW_NIGHT_ALONE", "minExperienced"):
            add("NEW_NIGHT_ALONE", "신입 N 지원 인원 부족")
    return out


def can_assign(r: Roster, n: NurseInfo, duty: Duty, d: date) -> bool:
    """고정된 미래 셀·월 경계를 포함해 이번 배정이 새 HARD 위반을 만드는지 검사한다."""
    candidate = r.with_cells({**r.cells, n.id: {**r.cells.get(n.id, {}), d: duty}})
    # 역순환·야간 휴식은 뒤 3일까지 영향을 준다. 연속 규칙은 최대 31일
    days = [day for day in r.days() if d <= day <= d + timedelta(days=31)]
    existing = {(v.rule_id, v.date) for day in days for v in assignment_violations(r, n, day)
                if r.rules.enforced(v.rule_id)}
    return not any(r.rules.enforced(v.rule_id) and (v.rule_id, v.date) not in existing
                   for day in days for v in assignment_violations(candidate, n, day))


def fill_day_fixed(r: Roster) -> Roster:
    """DAY_FIXED 간호사의 빈 칸을 평일 D, 주말·공휴일 O로 채운다. 연차·고정 칸은 그대로. 휴직이면 O"""
    cells: Cells = {k: dict(v) for k, v in r.cells.items()}
    for n in r.nurses:
        if not r.day_fixed(n):
            continue
        row = cells.setdefault(n.id, {})
        for d in r.days():
            if n.affiliated(d) and d not in row:
                row[d] = Duty.O if r.rest_day(d) or n.status == NurseStatus.ON_LEAVE else Duty.D
    return r.with_cells(cells)
