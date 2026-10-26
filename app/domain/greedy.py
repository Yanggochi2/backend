"""
GEN-01 대체 생성기 (CP-SAT이 시간 안에 해를 못 찾을 때). 날짜 순 그리디 편성. 입력 셀(연차 AL, GEN-05 고정 셀)은 그대로 두고 빈 칸만 채운다.
월 OFF 목표에서 계산한 개인별 근무 목표를 우선해 OFF가 일부 간호사에게 몰리지 않게 한다.
희망 오프는 후순위, 희망 근무는 해당 듀티에 우선 배정(소프트). 이전 달 말 근무(Roster.before)를 이어서 센다.
휴직(ON_LEAVE) 간호사는 배정하지 않는다(O).
"""
from app.domain.model import Cells, Duty, DutyRole, NurseStatus, Roster
from app.domain.rule_engine import can_assign
from app.domain.validator import coverage


def generate(r: Roster) -> Cells:
    cells: Cells = {n.id: dict(r.cells.get(n.id, {})) for n in r.nurses}
    cur = r.with_cells(cells)
    days = r.days()
    worked = {n.id: sum(1 for duty in cells[n.id].values() if duty.is_work) for n in r.nurses}
    work_goal = {
        n.id: max(0, sum(n.affiliated(d) for d in days) - r.off_target)
        for n in r.nurses
    }

    for d in r.days():
        for duty in (Duty.N, Duty.E, Duty.D):
            need = r.rules.required(duty) - coverage(cur, d).get(duty, 0)
            candidates = [n for n in r.nurses
                          if n.affiliated(d) and n.status != NurseStatus.ON_LEAVE and cells[n.id].get(d) is None]
            candidates.sort(key=lambda n: (
                duty == Duty.N and n.duty_role == DutyRole.NEW,   # 신입은 N에 마지막
                worked[n.id] >= work_goal[n.id],                  # OFF 목표를 이미 채운 사람은 후순위
                r.rules.enabled("WISH_OFF_IGNORED") and d in r.wish_offs.get(n.id, set()),  # 희망 오프 후순위
                r.rules.enabled("WISH_DUTY_IGNORED") and r.wish_duty(n.id, d) != duty,  # 희망 근무 우선
                -(work_goal[n.id] - worked[n.id]),                # 목표 대비 근무 부족이 큰 사람 우선
                worked[n.id],                                     # 근무 적은 사람 우선
            ))
            for n in candidates:
                if need <= 0:
                    break
                if not can_assign(cur, n, duty, d):
                    continue
                cells[n.id][d] = duty
                need -= 1
                worked[n.id] += 1
        for n in r.nurses:
            if n.affiliated(d):
                cells[n.id].setdefault(d, Duty.O)
    return cells
