"""
GEN-01. 날짜 순 그리디 편성. 입력 셀(연차 AL, GEN-05 고정 셀)은 그대로 두고 빈 칸만 채운다.
희망 오프는 후순위, 희망 근무는 해당 듀티에 우선 배정(소프트). 이전 달 말 근무(Roster.before)를 이어서 센다.
ponytail: 그리디라 최적해 보장 없음. 해 품질이 부족하면 OR-Tools CP-SAT으로 교체 (Yanggochi2/backend#26)
"""
from collections.abc import Callable
from datetime import date, timedelta

from app.domain.model import Cells, Duty, DutyRole, NurseInfo, NurseStatus, Roster
from app.domain.validator import coverage


def generate(r: Roster) -> Cells:
    cells: Cells = {n.id: dict(r.cells.get(n.id, {})) for n in r.nurses}
    cur = r.with_cells(cells)
    worked: dict = {}

    for d in r.days():
        for duty in (Duty.N, Duty.E, Duty.D):
            need = r.rules.required(duty) - coverage(cur, d).get(duty, 0)
            candidates = [n for n in r.nurses
                          if n.affiliated(d) and cells[n.id].get(d) is None and _can(cur, n, duty, d)]
            candidates.sort(key=lambda n: (
                duty == Duty.N and n.duty_role == DutyRole.NEW,   # 신입은 N에 마지막
                d in r.wish_offs.get(n.id, set()),                 # 희망 오프한 사람은 뒤로
                r.wish_duty(n.id, d) != duty,                      # 이 듀티를 희망한 사람은 앞으로
                worked.get(n.id, 0),                               # 근무 적은 사람 우선
            ))
            for n in candidates[:max(0, need)]:
                cells[n.id][d] = duty
                worked[n.id] = worked.get(n.id, 0) + 1
        for n in r.nurses:
            if n.affiliated(d):
                cells[n.id].setdefault(d, Duty.O)
    return cells


def _can(r: Roster, n: NurseInfo, duty: Duty, d: date) -> bool:
    """HARD로 켜진 규칙만 강제한다. SOFT·꺼진 규칙은 커버리지를 채우기 위해 어길 수 있다 (GEN-04 완화안)"""
    rules = r.rules
    if rules.enforced("CHARGE_DAY_ONLY") and n.duty_role == DutyRole.CHARGE and duty != Duty.D:
        return False
    if rules.enforced("PREGNANT_NIGHT") and n.status == NurseStatus.PREGNANT and duty == Duty.N:
        return False
    if (rules.enforced("NIGHT_TO_DAY") and rules.forbid_night_to_day and duty == Duty.D
            and r.cell(n.id, d - timedelta(days=1)) == Duty.N):
        return False
    if (rules.enforced("MAX_CONSECUTIVE_NIGHTS") and duty == Duty.N
            and _streak(r, n.id, d, lambda x: x == Duty.N) >= rules.max_consecutive_nights):
        return False
    return (not rules.enforced("MAX_CONSECUTIVE_WORK")
            or _streak(r, n.id, d, lambda x: x.is_work) < rules.max_consecutive_work_days)


def _streak(r: Roster, nurse_id, d: date, p: Callable[[Duty], bool]) -> int:
    k, x = 0, d - timedelta(days=1)
    while (v := r.cell(nurse_id, x)) is not None and p(v):
        k += 1
        x -= timedelta(days=1)
    return k
