"""병동 근무표 작성 유의사항: 평일 D 고정, 야간 3일 후 OFF 2일, 퐁당퐁당 금지, 근무별 평균 숙련도"""
from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.domain.generator import generate
from app.domain.model import Duty, DutyRole, NurseInfo, NurseStatus, Preset, Roster, Rules, Severity
from app.domain.rule_catalog import parameters
from app.domain.validator import coverage, off_target, skill_mix, unused_offs, validate

NOV = date(2026, 11, 1)  # 일요일 시작, 주말 9일
DAYS = [NOV + timedelta(days=i) for i in range(30)]
HOLIDAY = date(2026, 11, 18)  # 수요일을 병동 공휴일로
D, E, N, O, AL = Duty.D, Duty.E, Duty.N, Duty.O, Duty.AL


def nurse(i, role=DutyRole.GENERAL, status=NurseStatus.ACTIVE, head=False, skill=3, start=date(2025, 1, 1)):
    return NurseInfo(i, f"n{i}", role, status, start, head=head, skill_level=skill)


def rules(d=7, e=5, n=5, **settings):
    out = dict(Rules.preset(Preset.STANDARD, d, e, n).settings)
    for code, change in settings.items():
        out[code] = replace(out[code], **change)
    return Rules(out)


def ids(vs, severity=None):
    return {v.rule_id for v in vs if severity is None or v.severity == severity}


def ward():
    """수간호사 1 + 차지 2 + 인원에 세는 27명 (숙련도 1~5, 평균 약 3)"""
    ns = [nurse(0, head=True, skill=5), nurse(1, DutyRole.CHARGE, skill=5), nurse(2, DutyRole.CHARGE, skill=4)]
    # 차지가 평일 D에만 있으므로 주말·공휴일과 E·N의 고연차는 프리셉터가 맡는다
    ns += [nurse(i, DutyRole.PRECEPTOR, skill=(4, 5)[i % 2]) for i in range(3, 11)]
    ns += [nurse(i, DutyRole.NEW, skill=1) for i in range(11, 14)]
    ns += [nurse(i, skill=(2, 2, 3, 3)[i % 4]) for i in range(14, 30)]
    return ns


@pytest.fixture(scope="module")
def generated():
    r = Roster(NOV, ward(), {}, rules(), 11, holidays=frozenset({HOLIDAY}))
    out = r.with_cells(generate(r, max_seconds=40))
    return out, validate(out)


def test_full_ward_has_no_hard_violation(generated):
    out, vs = generated
    assert ids(vs, Severity.HARD) == set(), [v for v in vs if v.severity == Severity.HARD][:5]
    for d in DAYS:
        assert coverage(out, d).get(D) == 7 and coverage(out, d).get(E) == 5 and coverage(out, d).get(N) == 5


def test_head_and_charge_work_weekday_days_outside_coverage(generated):
    out, _ = generated
    for i in (0, 1, 2):
        for d in DAYS:
            rest = d.weekday() >= 5 or d == HOLIDAY
            assert out.cell(i, d) == (O if rest else D), f"{i} {d}"


def test_three_nights_then_two_offs(generated):
    out, _ = generated
    for n in out.nurses:
        xs = [out.cell(n.id, d) for d in DAYS]
        for i in range(len(xs) - 4):
            if xs[i:i + 3] == [N, N, N]:
                assert not xs[i + 3].is_work and not xs[i + 4].is_work, f"{n.id} NNN 뒤 OFF 2일"


def test_no_ping_pong(generated):
    out, _ = generated
    for n in out.nurses:
        xs = [out.cell(n.id, d) for d in DAYS]
        for a, b, c in zip(xs, xs[1:], xs[2:]):
            assert not (a.is_work and b.is_work and c.is_work and a != b != c), f"{n.id} 매일 다른 근무"
        for w in zip(xs, xs[1:], xs[2:], xs[3:]):
            work = [x.is_work for x in w]
            assert work not in ([True, False, True, False], [False, True, False, True]), f"{n.id} 근무·휴무 교대"


def test_skill_mix_near_three(generated):
    out, vs = generated
    avgs = [a for d in DAYS for a in skill_mix(out, d).values()]
    assert all(2.5 <= a <= 3.5 for a in avgs), sorted(avgs)[:3] + sorted(avgs)[-3:]
    assert "SKILL_MIX" not in ids(vs)


# --- 예외 처리 ---

def test_manual_cells_breaking_day_fixed_are_reported():
    ns = [nurse(0, head=True), nurse(1, DutyRole.CHARGE)]
    # 평일(11/2 월) N, 토요일(11/7) D → 둘 다 위반. 평일 연차·OFF는 허용
    cells = {0: {DAYS[1]: N, DAYS[2]: AL, DAYS[3]: O}, 1: {DAYS[6]: D}}
    vs = validate(Roster(NOV, ns, cells, rules(0, 0, 1), 0))
    fixed = {(v.nurse_id, v.date) for v in vs if v.rule_id == "DAY_FIXED"}
    assert fixed == {(0, DAYS[1]), (1, DAYS[6])}


def test_head_on_leave_or_joining_mid_month():
    ns = [nurse(0, head=True, status=NurseStatus.ON_LEAVE), nurse(1, DutyRole.CHARGE, start=DAYS[15])]
    ns += [nurse(i) for i in range(2, 8)]
    r = Roster(NOV, ns, {}, rules(1, 1, 1, PING_PONG={"enabled": False}), 8)
    out = r.with_cells(generate(r, max_seconds=3))
    assert {out.cell(0, d) for d in DAYS} == {O}, "휴직 수간호사는 모두 OFF"
    assert out.cell(1, DAYS[14]) is None and out.cell(1, DAYS[16]) == D, "소속 시작 전은 칸 없음, 이후 평일 D"


def test_fixed_cells_are_kept_for_day_fixed_nurses():
    """오프 신청 기능 대신 수간호사가 고정 칸으로 배치한 값은 그대로 둔다"""
    ns = [nurse(0, head=True)] + [nurse(i) for i in range(1, 8)]
    r = Roster(NOV, ns, {0: {DAYS[2]: O}}, rules(1, 1, 1), 8)
    assert r.with_cells(generate(r, max_seconds=3)).cell(0, DAYS[2]) == O


def test_day_fixed_off_counts_charge_in_coverage():
    ns = [nurse(1, DutyRole.CHARGE)]
    on = Roster(NOV, ns, {1: {DAYS[1]: D}}, rules(1, 0, 0), 0)
    off = replace(on, rules=rules(1, 0, 0, DAY_FIXED={"enabled": False}))
    assert coverage(on, DAYS[1]) == {} and coverage(off, DAYS[1]) == {D: 1}


def test_validator_detects_ping_pong_and_night_rest():
    ns = [nurse(1), nurse(2), nurse(3)]
    cells = {1: dict(zip(DAYS, [D, O, E, O])),          # 근무·휴무 교대
             2: dict(zip(DAYS, [D, E, N, O, O])),       # 매일 다른 근무
             3: dict(zip(DAYS, [N, N, N, O, D]))}       # 야간 3일 후 OFF 1일
    vs = validate(Roster(NOV, ns, cells, rules(0, 0, 1), 0))
    by = {(v.rule_id, v.nurse_id) for v in vs}
    assert {("PING_PONG", 1), ("PING_PONG", 2), ("NIGHT_REST", 3)} <= by
    # 2일 야간 뒤 OFF 1일은 기본(3일)에서는 허용, nights=2로 바꾸면 위반
    two = {3: dict(zip(DAYS, [N, N, O, D, D]))}
    assert "NIGHT_REST" not in ids(validate(Roster(NOV, ns, two, rules(0, 0, 1), 0)))
    strict = rules(0, 0, 1, NIGHT_REST={"parameters": {"nights": 2}})
    assert "NIGHT_REST" in ids(validate(Roster(NOV, ns, two, strict, 0)))


def test_night_rest_continues_from_last_month():
    ns = [nurse(i) for i in range(1, 9)]
    before = {1: {NOV - timedelta(days=k): N for k in (1, 2, 3)}}
    r = Roster(NOV, ns, {}, rules(1, 1, 1), 8, before=before)
    out = r.with_cells(generate(r, max_seconds=3))
    assert out.cell(1, DAYS[0]) == O and out.cell(1, DAYS[1]) == O


def test_skill_mix_reports_low_average():
    ns = [nurse(1, skill=1), nurse(2, skill=2)]
    vs = validate(Roster(NOV, ns, {1: {DAYS[0]: D}, 2: {DAYS[0]: D}}, rules(2, 0, 0), 0))
    assert any(v.rule_id == "SKILL_MIX" and v.date == DAYS[0] and v.severity == Severity.SOFT for v in vs)


@pytest.mark.parametrize("code,bad", [("NIGHT_REST", {"nights": 0}), ("SKILL_MIX", {"target": 6}),
                                      ("SKILL_MIX", {"target": "3"})])
def test_rejects_bad_parameters(code, bad):
    with pytest.raises(ValueError):
        parameters(code, bad)


# --- 월 경계: 지난달 말 나이트와 이어지는 나이트 3일 ---

LAST = NOV - timedelta(days=1)


@pytest.mark.parametrize("before,fixed,rest", [
    ({LAST - timedelta(days=1): N, LAST: N}, {DAYS[0]: N}, (DAYS[1], DAYS[2])),   # 지난달 N N + 1일 N
    ({LAST: N}, {DAYS[0]: N, DAYS[1]: N}, (DAYS[2], DAYS[3])),                   # 지난달 N + 1·2일 N N
])
def test_three_nights_across_months_get_two_offs(before, fixed, rest):
    ns = [nurse(i) for i in range(1, 9)]
    r = Roster(NOV, ns, {1: fixed}, rules(1, 1, 1), 8, before={1: before})
    out = r.with_cells(generate(r, max_seconds=3))
    assert all(out.cell(1, d) == O for d in rest), [out.cell(1, d) for d in DAYS[:5]]
    assert "NIGHT_REST" not in ids(validate(out))


def test_validator_counts_last_month_nights():
    ns = [nurse(1)]
    before = {1: {LAST - timedelta(days=k): N for k in (0, 1)}}
    r = Roster(NOV, ns, {1: {DAYS[0]: N, DAYS[1]: O, DAYS[2]: D}}, rules(0, 0, 1), 0, before=before)
    assert any(v.rule_id == "NIGHT_REST" and v.date == DAYS[2] for v in validate(r))


# --- 다 못 쓴 OFF 이월 ---

def test_unused_offs_carry_into_next_month_target():
    ns = [nurse(1), nurse(2), nurse(3, start=DAYS[10]), nurse(0, head=True)]
    # 1번 OFF 8일(목표 10 → 2일 남음), 2번 OFF 11일(초과는 깎지 않음), 3번은 월중 소속이라 제외, 수간호사 제외
    cells = {1: {d: (O if i < 8 else D) for i, d in enumerate(DAYS)},
             2: {d: (O if i < 11 else D) for i, d in enumerate(DAYS)}}
    prev = Roster(NOV, ns, cells, rules(1, 0, 0), 10, off_carry={1: 0})
    assert unused_offs(prev) == {1: 2}
    dec = Roster(date(2026, 12, 1), ns, {}, rules(1, 0, 0), 10, off_carry=unused_offs(prev))
    assert off_target(dec, ns[0]) == 12 and off_target(dec, ns[1]) == 10
    # 이월분까지 다 못 쓰면 누적된다
    assert unused_offs(replace(dec, cells={1: {d: (O if i < 9 else D) for i, d in enumerate(dec.days())}}))[1] == 3


def test_generator_gives_carried_offs():
    ns = [nurse(i) for i in range(1, 11)]
    r = Roster(NOV, ns, {}, rules(2, 2, 2), 10, off_carry={1: 3})
    out = r.with_cells(generate(r, max_seconds=5))
    offs = sum(out.cell(1, d) == O for d in DAYS)
    assert 12 <= offs <= 14, offs
    assert not any(v.rule_id == "OFF_TARGET" and v.nurse_id == 1 for v in validate(out))
