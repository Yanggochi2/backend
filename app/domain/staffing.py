"""
가용 인력에 맞는 듀티별 하루 인원(D/E/N) 추천.
필요 인원이 인력보다 적으면 남는 근무일을 연차로 메우게 되고, 많으면 O를 못 쓴다. 공급(근무 가능 일수)에 맞춘다
- 공급 = 교대 간호사별 (소속 일수 - O 목표 - 연차·교육 칸) 합. 수간호사·차지(평일 D 고정)·간호조무사·휴직자는 뺀다
- 하루 인원 T = 공급 // 일수 (내림: 매일 최소 인원은 반드시 채우고, 나머지는 생성기가 D에 나눠 둔다)
- 나누기는 D ≥ E ≥ N. N이 가장 힘든 근무라 가장 적게 두고, 야간 가능 인원·월 야간 상한을 넘지 않게 자른다
"""
from app.domain.model import NIGHT_ONLY_MAX_NIGHTS, Duty, DutyRole, NurseStatus, Roster, StaffType
from app.domain.validator import off_target


def recommend(r: Roster) -> dict:
    days = r.days()
    supply = night_cap = 0
    for n in r.nurses:
        if n.staff_type != StaffType.NURSE or n.status == NurseStatus.ON_LEAVE or n.weekday_only:
            continue
        mine = [d for d in days if n.affiliated(d)]
        fixed = sum(r.cell(n.id, d) in (Duty.AL, Duty.ED) for d in mine)
        work = max(0, len(mine) - off_target(r, n) - fixed)
        supply += work
        if n.duty_role == DutyRole.NIGHT_ONLY:
            night_cap += min(work, NIGHT_ONLY_MAX_NIGHTS)
        elif n.status != NurseStatus.PREGNANT:
            night_cap += min(work, r.rules.parameter("MAX_MONTHLY_NIGHTS", "max"))
    total = supply // len(days)
    night = min(total // 3, night_cap // len(days))
    evening = (total - night) // 2
    rec = {"D": total - night - evening, "E": evening, "N": night}
    current = {x.value: r.rules.required(x) for x in (Duty.D, Duty.E, Duty.N)}
    demand = sum(current.values()) * len(days)
    return {"recommended": rec, "current": current, "supply": supply, "demand": demand,
            # 양수: 남는 근무일(연차·초과 D로 흘러감), 음수: 모자란 근무일(O 미사용·이월)
            "surplus": supply - demand}


if __name__ == "__main__":
    from datetime import date

    from app.domain.model import NurseInfo, Preset, Rules

    def roster(k, d=4, e=4, n=4, status=NurseStatus.ACTIVE):
        nurses = [NurseInfo(i, f"n{i}", DutyRole.GENERAL, status, date(2025, 1, 1)) for i in range(k)]
        return Roster(date(2026, 11, 1), nurses, {}, Rules.preset(Preset.STANDARD, d, e, n), 10)

    # 22명 × (30 - 10) = 440일 → 하루 14명: N 4, E 5, D 5
    out = recommend(roster(22))
    assert out["recommended"] == {"D": 5, "E": 5, "N": 4} and out["surplus"] == 440 - 12 * 30, out
    # 연차 칸은 공급에서 빠진다
    r = roster(22)
    r = r.with_cells({0: {date(2026, 11, d): Duty.AL for d in range(1, 21)}})
    assert recommend(r)["supply"] == 420
    # 임신 간호사만 있으면 N 0
    assert recommend(roster(6, status=NurseStatus.PREGNANT))["recommended"]["N"] == 0
    print("ok")
