"""
GEN-01. 날짜 순 그리디 편성. 입력 셀(연차 AL, GEN-05 고정 셀)은 그대로 두고 빈 칸만 채운다.
희망 오프는 후순위, 희망 근무는 해당 듀티에 우선 배정(소프트). 이전 달 말 근무(Roster.before)를 이어서 센다.
휴직(ON_LEAVE) 간호사는 배정하지 않는다(O).
ponytail: 그리디라 최적해 보장 없음. 해 품질이 부족하면 OR-Tools CP-SAT으로 교체 (Yanggochi2/backend#26)
"""
from app.domain.model import Cells, Duty, DutyRole, NurseStatus, Roster
from app.domain.rule_engine import can_assign
from app.domain.validator import coverage


def generate(r: Roster) -> Cells:
    cells: Cells = {n.id: dict(r.cells.get(n.id, {})) for n in r.nurses}
    cur = r.with_cells(cells)
    worked: dict = {}

    for d in r.days():
        for duty in (Duty.N, Duty.E, Duty.D):
            need = r.rules.required(duty) - coverage(cur, d).get(duty, 0)
            candidates = [n for n in r.nurses
                          if n.affiliated(d) and n.status != NurseStatus.ON_LEAVE and cells[n.id].get(d) is None]
            candidates.sort(key=lambda n: (
                duty == Duty.N and n.duty_role == DutyRole.NEW,   # 신입은 N에 마지막
                r.rules.enabled("WISH_OFF_IGNORED") and d in r.wish_offs.get(n.id, set()),  # 희망 오프 후순위
                r.rules.enabled("WISH_DUTY_IGNORED") and r.wish_duty(n.id, d) != duty,  # 희망 근무 우선
                worked.get(n.id, 0),                               # 근무 적은 사람 우선
            ))
            for n in candidates:
                if need <= 0:
                    break
                if not can_assign(cur, n, duty, d):
                    continue
                cells[n.id][d] = duty
                need -= 1
                worked[n.id] = worked.get(n.id, 0) + 1
        for n in r.nurses:
            if n.affiliated(d):
                cells[n.id].setdefault(d, Duty.O)
    return cells
