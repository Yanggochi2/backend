"""병동 근무표 작성 유의사항: 야간 3일 후 OFF 2일(월 경계 포함), 퐁당퐁당 금지, 근무별 평균 숙련도, 못 쓴 O 이월"""
from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.domain.generator import generate
from app.domain.model import Duty, DutyRole, NurseInfo, NurseStatus, Preset, Role, Roster, Rules, Severity
from app.domain.rule_catalog import parameters
from app.domain.validator import off_target, skill_mix, unused_offs, validate

NOV = date(2026, 11, 1)  # 일요일 시작, 주말 9일
DAYS = [NOV + timedelta(days=i) for i in range(30)]
LAST = NOV - timedelta(days=1)
D, E, N, O = Duty.D, Duty.E, Duty.N, Duty.O


def nurse(i, skill=3, start=date(2025, 1, 1), role=Role.NURSE):
    return NurseInfo(i, f"n{i}", DutyRole.GENERAL, NurseStatus.ACTIVE, start, role=role, skill_level=skill)


def rules(d=1, e=1, n=1, **settings):
    out = dict(Rules.preset(Preset.STANDARD, d, e, n).settings)
    for code, change in settings.items():
        out[code] = replace(out[code], **change)
    return Rules(out)


def ids(vs):
    return {v.rule_id for v in vs}


def test_validator_detects_ping_pong_and_three_night_rest():
    ns = [nurse(1), nurse(2), nurse(3)]
    cells = {1: dict(zip(DAYS, [D, O, E, O])),          # 근무·휴무 교대
             2: dict(zip(DAYS, [D, E, N, O, O])),       # 매일 다른 근무
             3: dict(zip(DAYS, [N, N, N, O, D]))}       # 야간 3일 후 OFF 1일
    by = {(v.rule_id, v.nurse_id) for v in validate(Roster(NOV, ns, cells, rules(0, 0, 1), 0))}
    assert {("PING_PONG", 1), ("PING_PONG", 2), ("NIGHT_REST", 3)} <= by
    # 2일 야간 뒤 OFF 1일은 기본(3일)에서는 허용, nights=2로 바꾸면 위반
    two = {3: dict(zip(DAYS, [N, N, O, D, D]))}
    assert "NIGHT_REST" not in ids(validate(Roster(NOV, ns, two, rules(0, 0, 1), 0)))
    strict = rules(0, 0, 1, NIGHT_REST={"parameters": {"nights": 2}})
    assert "NIGHT_REST" in ids(validate(Roster(NOV, ns, two, strict, 0)))


@pytest.mark.parametrize("before,fixed,rest", [
    ({LAST - timedelta(days=1): N, LAST: N}, {DAYS[0]: N}, (DAYS[1], DAYS[2])),   # 지난달 N N + 1일 N
    ({LAST: N}, {DAYS[0]: N, DAYS[1]: N}, (DAYS[2], DAYS[3])),                   # 지난달 N + 1·2일 N N
])
def test_three_nights_across_months_get_two_offs(before, fixed, rest):
    ns = [nurse(i) for i in range(1, 9)]
    r = Roster(NOV, ns, {1: fixed}, rules(), 10, before={1: before})
    out = r.with_cells(generate(r, max_seconds=3))
    assert all(out.cell(1, d) == O for d in rest), [out.cell(1, d) for d in DAYS[:5]]
    assert "NIGHT_REST" not in ids(validate(out))


def test_generator_avoids_ping_pong():
    ns = [nurse(i) for i in range(1, 9)]
    r = Roster(NOV, ns, {}, rules(), 10)
    out = r.with_cells(generate(r, max_seconds=5))
    assert "PING_PONG" not in ids(validate(out))


def test_skill_mix_near_target():
    # 숙련도 1·5 각 6명: 근무마다 평균 3이 되려면 섞어야 한다
    ns = [nurse(i, skill=1) for i in range(6)] + [nurse(i, skill=5) for i in range(6, 12)]
    r = Roster(NOV, ns, {}, rules(2, 2, 2), 10)
    out = r.with_cells(generate(r, max_seconds=8))
    avgs = [a for d in DAYS for a in skill_mix(out, d).values()]
    assert all(2.5 <= a <= 3.5 for a in avgs), sorted(avgs)[:3] + sorted(avgs)[-3:]
    low = Roster(NOV, ns, {0: {DAYS[0]: D}, 1: {DAYS[0]: D}}, rules(2, 0, 0), 0)
    assert any(v.rule_id == "SKILL_MIX" and v.date == DAYS[0] and v.severity == Severity.SOFT for v in validate(low))


def test_unused_offs_carry_into_next_month_target():
    ns = [nurse(1), nurse(2), nurse(0, role=Role.HEAD_NURSE)]
    # 1번 O 8일(목표 10 → 2일 남음), 2번 O 11일(초과는 깎지 않음), 수간호사는 평일 D 고정이라 제외
    cells = {1: {d: (O if i < 8 else D) for i, d in enumerate(DAYS)},
             2: {d: (O if i < 11 else D) for i, d in enumerate(DAYS)}}
    prev = Roster(NOV, ns, cells, rules(1, 0, 0), 10)
    assert unused_offs(prev) == {1: 2}
    dec = Roster(date(2026, 12, 1), ns, {}, rules(1, 0, 0), 10, off_carry=unused_offs(prev))
    assert off_target(dec, ns[0]) == 12 and off_target(dec, ns[1]) == 10


def test_generator_gives_carried_offs():
    ns = [nurse(i) for i in range(1, 11)]
    r = Roster(NOV, ns, {}, rules(2, 2, 2), 10, off_carry={1: 2})
    out = r.with_cells(generate(r, max_seconds=5))
    assert sum(out.cell(1, d) == O for d in DAYS) == 12
    assert not any(v.rule_id == "OFF_TARGET" and v.nurse_id == 1 for v in validate(out))


@pytest.mark.parametrize("code,bad", [("NIGHT_REST", {"nights": 0}), ("SKILL_MIX", {"target": 6}),
                                      ("SKILL_MIX", {"target": "3"})])
def test_rejects_bad_parameters(code, bad):
    with pytest.raises(ValueError):
        parameters(code, bad)
