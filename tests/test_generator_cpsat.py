"""GEN-01 CP-SAT 생성기: 하드 규칙을 지키는지, 30명 병동에서 품질이 나오는지"""
from datetime import date, timedelta

from app.domain.generator import generate
from app.domain.model import Duty, DutyRole, NurseInfo, NurseStatus, Roster, Rules, Severity
from app.domain.validator import validate

NOV = date(2026, 11, 1)
DAYS = [NOV + timedelta(days=i) for i in range(30)]
REVERSE = {(Duty.E, Duty.D), (Duty.N, Duty.D), (Duty.N, Duty.E)}


def nurse(i, role=DutyRole.GENERAL, status=NurseStatus.ACTIVE):
    return NurseInfo(i, f"n{i}", role, status, date(2025, 1, 1))


def run(r: Roster, seconds=1):
    out = r.with_cells(generate(r, max_seconds=seconds))
    return out, validate(out)


def hard(vs):
    return [v for v in vs if v.severity == Severity.HARD]


def seq(out, nurse_id):
    return [out.cell(nurse_id, d) for d in DAYS]


def test_small_ward_keeps_every_hard_rule():
    ns = [nurse(1, DutyRole.CHARGE), nurse(2, status=NurseStatus.PREGNANT), nurse(3, DutyRole.NEW)]
    ns += [nurse(i) for i in range(4, 16)]
    fixed = {5: {DAYS[9]: Duty.AL, DAYS[10]: Duty.AL}, 6: {DAYS[3]: Duty.N}}
    # 차지 1명이라 근무마다 차지는 불가능 → '차지는 D만' 규칙으로 검사
    rules = Rules(2, 2, 2, 3, 5, True, severity={"CHARGE_DAY_ONLY": Severity.HARD, "CHARGE_EVERY_SHIFT": None,
                                                  "SENIOR_EVERY_SHIFT": None})
    out, vs = run(Roster(NOV, ns, fixed, rules, 8))
    assert hard(vs) == []
    assert out.cell(5, DAYS[9]) == Duty.AL and out.cell(6, DAYS[3]) == Duty.N, "연차·고정 칸 유지"
    for n in ns:
        xs = seq(out, n.id)
        assert all((a, b) not in REVERSE for a, b in zip(xs, xs[1:])), f"{n.id} 역순환"
        for i in range(1, len(xs) - 2):
            if xs[i - 1] == xs[i] == Duty.N and xs[i + 1] != Duty.N:
                assert not xs[i + 1].is_work and not xs[i + 2].is_work, f"{n.id} 야간 후 휴식"
    assert all(x == Duty.D for x in seq(out, 1) if x.is_work), "차지는 D만"
    assert Duty.N not in seq(out, 2), "임신 중 야간 없음"


def test_continues_last_month_nights():
    ns = [nurse(i) for i in range(1, 13)]
    # 지난달 말 N N N → 연속 야간 3일을 채웠으므로 1·2일은 OFF (야간 후 휴식)
    before = {1: {NOV - timedelta(days=k): Duty.N for k in (1, 2, 3)}}
    out, vs = run(Roster(NOV, ns, {}, Rules(2, 2, 2, 3, 5, True), 8, before=before))
    assert hard(vs) == [] and out.cell(1, DAYS[0]) == Duty.O and out.cell(1, DAYS[1]) == Duty.O


def test_on_leave_nurse_is_off_and_shortage_still_returns_cells():
    ns = [nurse(1), nurse(2, status=NurseStatus.ON_LEAVE)]
    out, vs = run(Roster(NOV, ns, {}, Rules(1, 1, 1, 3, 5, True), 8))
    assert set(seq(out, 2)) == {Duty.O}
    assert "COVERAGE" in {v.rule_id for v in hard(vs)}, "인원 부족은 하드 위반으로 남는다"
    assert all(x is not None for x in seq(out, 1))


def test_thirty_nurse_ward_quality():
    roles = [DutyRole.CHARGE] * 6 + [DutyRole.PRECEPTOR] * 3 + [DutyRole.NEW] * 3 + [DutyRole.GENERAL] * 18
    ns = [nurse(i, role) for i, role in enumerate(roles)]
    ns[15] = nurse(15, status=NurseStatus.PREGNANT)
    wish = {i: {DAYS[i % 30]} for i in range(0, 30, 3)}
    r = Roster(NOV, ns, {}, Rules(6, 5, 4, 3, 5, True), 9, wish_offs=wish)
    out, vs = run(r, seconds=10)
    by = {}
    for v in vs:
        by.setdefault(v.rule_id, []).append(v)
    assert hard(vs) == []
    assert len(by.get("MAX_MONTHLY_NIGHTS", [])) == 0, "월 야간 상한 6 이하"
    assert len(by.get("WISH_OFF_IGNORED", [])) == 0, "희망 오프 반영"
    assert len(by.get("ISOLATED_SHIFT", [])) <= len(ns), "고립 배치는 1인당 1건 이하 수준"


def test_charge_every_shift_and_night_only_nurses():
    roles = [DutyRole.CHARGE] * 6 + [DutyRole.NIGHT_ONLY] * 2 + [DutyRole.PRECEPTOR] * 3 + [DutyRole.NEW] * 3
    roles += [DutyRole.GENERAL] * 16
    ns = [nurse(i, role) for i, role in enumerate(roles)]
    out, vs = run(Roster(NOV, ns, {}, Rules(6, 5, 4, 3, 5, True), 9), seconds=10)
    assert hard(vs) == []
    for d in DAYS:
        for x in (Duty.D, Duty.E, Duty.N):
            assert any(out.cell(n.id, d) == x for n in ns if n.duty_role == DutyRole.CHARGE), f"{d} {x} 차지"
    for i in (6, 7):
        xs = seq(out, i)
        assert set(xs) <= {Duty.N, Duty.O} and xs.count(Duty.N) <= 15, "나이트 전담"
