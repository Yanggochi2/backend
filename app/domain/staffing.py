"""
가용 인력에 맞는 듀티별 하루 인원(D/E/N) 추천.
필요 인원이 인력보다 적으면 남는 근무일을 연차로 메우게 되고, 많으면 O를 못 쓴다. 공급(근무 가능 일수)에 맞춘다
- 공급 = 교대 간호사별 (소속 일수 - O 목표 - 연차·교육 칸) 합. 간호조무사·휴직자는 뺀다
- 수간호사·차지는 평일 D 고정이라 그날 D 필요 인원을 먼저 채운다 (주말·공휴일은 교대 간호사가 모두 채운다)
- 매일 최소 인원을 채울 수 있는 가장 큰 하루 인원을 고른다. 남는 근무일은 생성기가 D에 나눠 둔다
- 나누기는 D ≥ E > N (N이 가장 힘든 근무라 가장 적게). 야간 가능 인원·월 야간 상한을 넘지 않게 자른다
"""
from app.domain.model import NIGHT_ONLY_MAX_NIGHTS, Duty, DutyRole, NurseStatus, Roster, StaffType
from app.domain.validator import off_target


def recommend(r: Roster) -> dict:
    days = r.days()
    supply = night_cap = 0
    fixed_d = {d: 0 for d in days}  # 날마다 수간호사·차지가 채우는 D
    for n in r.nurses:
        if n.staff_type != StaffType.NURSE or n.status == NurseStatus.ON_LEAVE:
            continue
        if n.weekday_only:
            # 평일에 실제로 D(또는 아직 빈 칸)인 날만. 연차·교육·O로 빠진 날은 교대 간호사가 채운다
            for d in days:
                fixed_d[d] += (n.affiliated(d) and d.weekday() < 5 and d not in r.holidays
                               and r.cell(n.id, d) in (None, Duty.D))
            continue
        mine = [d for d in days if n.affiliated(d) and r.cell(n.id, d) not in (Duty.AL, Duty.ED)]
        work = max(0, len(mine) - off_target(r, n))
        supply += work
        if n.duty_role == DutyRole.NIGHT_ONLY:
            night_cap += min(work, NIGHT_ONLY_MAX_NIGHTS)
        elif n.status != NurseStatus.PREGNANT:
            night_cap += min(work, r.rules.parameter("MAX_MONTHLY_NIGHTS", "max"))

    def demand(x: dict) -> int:
        """교대 간호사가 채워야 하는 근무일"""
        return sum(x["E"] + x["N"] + max(0, x["D"] - fixed_d[d]) for d in days)

    def split(total: int) -> dict:
        night = min(max(0, total - 1) // 3, night_cap // len(days))
        evening = (total - night) // 2
        return {"D": total - night - evening, "E": evening, "N": night}

    total = 0
    # ponytail: 하루 인원을 1씩 올려 보는 선형 탐색. 병동 인원 수십 명이라 충분
    while demand(split(total + 1)) <= supply:
        total += 1
    current = {x.value: r.rules.required(x) for x in (Duty.D, Duty.E, Duty.N)}
    return {"recommended": split(total), "current": current, "supply": supply, "demand": demand(current),
            # 양수: 남는 근무일(연차·초과 D로 흘러감), 음수: 모자란 근무일(O 미사용·이월)
            "surplus": supply - demand(current)}


if __name__ == "__main__":
    from datetime import date

    from app.domain.model import NurseInfo, Preset, Rules

    def roster(k, d=4, e=4, n=4, status=NurseStatus.ACTIVE, charges=0):
        nurses = [NurseInfo(i, f"n{i}", DutyRole.GENERAL, status, date(2025, 1, 1)) for i in range(k)]
        nurses += [NurseInfo(k + i, f"c{i}", DutyRole.CHARGE, status, date(2025, 1, 1)) for i in range(charges)]
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
    # 차지 2명은 평일(2026-11: 21일) D를 채운다 → 교대 간호사 몫 360 - 2×21
    out = recommend(roster(22, charges=2))
    assert out["demand"] == 360 - 42 and out["surplus"] == 440 - 318, out
    assert out["recommended"] == {"D": 6, "E": 6, "N": 4}, out  # 평일 D 4명은 차지 2명 + 교대 4명
    # 평일 D 칸을 O로 바꾼 날은 차지가 D를 채우지 않는다
    r = roster(22, charges=1)
    r = r.with_cells({22: {date(2026, 11, 2): Duty.O}})
    assert recommend(r)["demand"] == 360 - 20
    # 하루 18명이면 N이 D·E와 같지 않게 (6/6/6 대신 7/6/5)
    assert recommend(roster(27, charges=0))["recommended"] == {"D": 7, "E": 6, "N": 5}
    print("ok")
