"""생성 후보와 저장된 근무표가 공유하는 배정 규칙."""
from datetime import date, timedelta

from app.domain.model import Duty, DutyRole, NurseInfo, NurseStatus, Roster, Violation


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
    p1, p2, p3 = (r.cell(n.id, d - k * one) for k in (1, 2, 3))
    if (p1, x) in REVERSE:
        add("NIGHT_TO_DAY", f"역순환 {p1}→{x} (근무 사이 휴식 11시간 미만)")
    # 야간 2일 이상 연속 후 48시간(OFF 2일) 휴식
    if x is not None and x.is_work and ((x != Duty.N and p1 == Duty.N and p2 == Duty.N)
                                        or (p1 != Duty.N and p2 == Duty.N and p3 == Duty.N)):
        add("NIGHT_REST", "야간 2일 연속 후 OFF 2일 필요")
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
