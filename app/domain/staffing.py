"""
가용 인력에 맞는 듀티별 하루 인원(D/E/N) 추천.
필요 인원이 인력보다 적으면 남는 근무일을 연차로 메우게 되고, 많으면 O를 못 쓴다. 공급(근무 가능 일수)에 맞춘다
- 공급 = 교대 간호사별 (소속 일수 - O 목표 - 연차·교육 칸) 합. 수간호사·차지(평일 D 고정)·간호조무사·휴직자는 뺀다
- 하루 인원 T = 공급 // 일수 (내림: 매일 최소 인원은 반드시 채우고, 나머지는 생성기가 D에 나눠 둔다)
- 나누기는 D ≥ E ≥ N. N이 가장 힘든 근무라 가장 적게 두고, 야간 가능 인원·월 야간 상한을 넘지 않게 자른다
"""
from math import ceil

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
    # 수요는 교대 간호사 몫만: 커버리지는 평일 D 고정 인원도 D로 세므로 그날 그만큼 D 필요 인원에서 뺀다
    fixed_d = [n for n in r.nurses
               if n.staff_type == StaffType.NURSE and n.status != NurseStatus.ON_LEAVE and n.weekday_only]
    demand = 0
    for d in days:
        demand += sum(current.values())
        if d.weekday() < 5 and d not in r.holidays:
            on_d = sum(n.affiliated(d) and r.cell(n.id, d) in (None, Duty.D) for n in fixed_d)
            demand -= min(current["D"], on_d)
    return {"recommended": rec, "current": current, "supply": supply, "demand": demand, "nightCapacity": night_cap,
            # 양수: 남는 근무일(연차·초과 D로 흘러감), 음수: 모자란 근무일(O 미사용·이월)
            "surplus": supply - demand}


def shortage(r: Roster) -> dict | None:
    """
    지금 인원으로 하루 필요 인원(D/E/N)을 한 달 내내 채울 수 없으면 부족분, 넉넉하면 None.
    nursesNeeded: 모자란 근무일을 교대 간호사 한 명의 한 달 근무일(일수 - O 목표)로 나눠 올림한 추가 인원
    """
    rec = recommend(r)
    if rec["surplus"] >= 0:
        return None
    short = -rec["surplus"]
    return {**rec, "shortDays": short, "nursesNeeded": ceil(short / max(1, len(r.days()) - r.off_target))}


def targets(r: Roster) -> dict:
    """
    날짜별 D/E/N 적정 인원. 기본은 필요 인원이고, 인력이 남으면(O 목표를 지키고도 근무일이 남으면) 하루에 더 넣을
    인원을 남는 근무일 ÷ 일수의 **올림**으로 정해 매일 같은 수로 고정한다 (11/23 미팅: "딱 고정").
    그날 안에서는 필요 인원 비율대로 D/E에 나눈다. N은 남아도 올리지 않는다: 가장 힘든 근무이고, 야간 가능 인원에
    딱 맞게 올리면 월 야간 상한을 넘는 사람이 생긴다 (데모 병동 N 5명 고정 시 2명 초과).
    올림으로 모자라는 근무일은 O를 덜 줘서 채우고(off_cut), 덜 준 O는 확정 후 다음 달로 이월된다.
    """
    days = r.days()
    base = {x: r.rules.required(x) for x in (Duty.D, Duty.E, Duty.N)}
    rec = recommend(r)
    extra, total = max(0, rec["surplus"]), sum(base.values())
    if not extra or not total:
        return {d: dict(base) for d in days}
    units = ceil(extra / len(days))
    day = base[Duty.D] + base[Duty.E]
    if not day:  # D/E 필요 인원이 없으면 남는 인원을 어디에도 올리지 않는다
        return {d: dict(base) for d in days}
    share = {Duty.D: units * base[Duty.D] // day, Duty.N: 0}
    share[Duty.E] = units - share[Duty.D]
    # 나머지는 비율의 소수점이 큰 쪽으로 (같으면 D)
    if share[Duty.E] and units * base[Duty.D] % day * 2 >= day:
        share[Duty.D], share[Duty.E] = share[Duty.D] + 1, share[Duty.E] - 1
    return {d: {x: base[x] + share[x] for x in base} for d in days}


def off_cut(r: Roster) -> tuple[int, int]:
    """
    적정 인원을 매일 같게(올림) 맞추느라 모자라는 근무일을 채우려고 교대 간호사 한 명당 덜 주는 O 개수 (최소, 최대).
    모자란 근무일을 교대 간호사 수로 나눈 내림·올림이라 사람마다 많아야 1개 차이. 덜 준 O는 확정 후 다음 달 O 목표로
    이월된다 (validator.unused_offs)
    """
    rec = recommend(r)
    days = r.days()
    base = sum(r.rules.required(x) for x in (Duty.D, Duty.E, Duty.N))
    extra = max(0, rec["surplus"])
    if not extra or not base:
        return 0, 0
    short = sum(sum(v.values()) for v in targets(r).values()) - base * len(days) - extra
    shift = sum(1 for n in r.nurses if n.staff_type == StaffType.NURSE and n.status != NurseStatus.ON_LEAVE
                and not n.weekday_only)
    if short <= 0 or not shift:
        return 0, 0
    return short // shift, ceil(short / shift)


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
    # 차지의 평일 D는 커버리지에 들어가므로 수요에서 뺀다 (2026-11 평일 21일)
    r = roster(22)
    r = Roster(r.month, r.nurses + [NurseInfo(99, "c", DutyRole.CHARGE, NurseStatus.ACTIVE, date(2025, 1, 1))],
               {}, r.rules, 10)
    out = recommend(r)
    assert out["supply"] == 440 and out["demand"] == 12 * 30 - 21, out
    # 임신 간호사만 있으면 N 0
    assert recommend(roster(6, status=NurseStatus.PREGNANT))["recommended"]["N"] == 0
    # 수요 12×30=360일. 18명×20일=360이면 딱 맞아 통과, 17명은 340일이라 20일 부족 → 1명 더 필요
    assert shortage(roster(22)) is None and shortage(roster(18)) is None
    out = shortage(roster(17))
    assert out["shortDays"] == 20 and out["nursesNeeded"] == 1, out
    # 적정 인원: 남는 80일 ÷ 30일 → 하루 3명 올림, D:E = 4:4라 1.5씩 → 반올림으로 D 2, E 1, N은 그대로 → 매일 6/5/4
    r = roster(22)
    assert set(map(lambda v: tuple(v.values()), targets(r).values())) == {(6, 5, 4)}
    # 하루 3명 × 30일 = 90일인데 남는 건 80일 → 10일 모자람 → 22명이 O를 최대 1개 덜 쉰다
    assert off_cut(r) == (0, 1)
    assert off_cut(roster(18)) == (0, 0), "남는 게 없으면 O를 줄이지 않는다"
    assert targets(roster(17)) == {d: {Duty.D: 4, Duty.E: 4, Duty.N: 4} for d in r.days()}, "모자라면 필요 인원 그대로"
    print("ok")
