from datetime import date, timedelta

from app.domain.generator import generate
from app.domain.model import Duty, DutyRole, NurseInfo, NurseStatus, Preset, Roster, Rules, Severity
from app.domain.validator import validate

OCT = date(2026, 10, 1)
START = OCT


def nurse(i, role=DutyRole.GENERAL, status=NurseStatus.ACTIVE):
    return NurseInfo(i, f"n{i}", role, status, START)


def ids(vs):
    return {v.rule_id for v in vs}


def day(n):
    return START + timedelta(days=n)


def test_detects_hard_violations():
    rules = Rules(0, 0, 0, 1, 31, True)
    ns = [nurse(1, DutyRole.CHARGE), nurse(2, DutyRole.NEW), nurse(3, status=NurseStatus.PREGNANT)]
    cells = {1: {START: Duty.N}, 2: {day(1): Duty.N}, 3: {START: Duty.N, day(1): Duty.N, day(2): Duty.D}}
    # 신입(2)은 같은 날 임신자(3)가 N에 있어 단독이 아님
    assert ids(validate(Roster(OCT, ns, cells, rules, 0))) == {
        "CHARGE_DAY_ONLY", "PREGNANT_NIGHT", "MAX_CONSECUTIVE_NIGHTS", "NIGHT_TO_DAY"}
    alone = Roster(OCT, [ns[1]], {2: {START: Duty.N}}, rules, 0)
    assert "NEW_NIGHT_ALONE" in ids(validate(alone))


def test_generator_satisfies_hard_rules_when_staff_is_enough():
    ns = [nurse(1, DutyRole.CHARGE), nurse(2, DutyRole.NEW), nurse(3, status=NurseStatus.PREGNANT)]
    ns += [nurse(i) for i in range(4, 15)]
    rules = Rules.preset(Preset.STANDARD, 3, 2, 2)
    cells = generate(Roster(OCT, ns, {5: {day(9): Duty.AL}}, rules, 0))
    out = Roster(OCT, ns, cells, rules, 0)
    assert out.cell(5, day(9)) == Duty.AL, "연차는 유지"
    assert [v for v in validate(out) if v.severity == Severity.HARD] == []


def test_reports_coverage_shortage_when_staff_is_short():
    rules = Rules.preset(Preset.STANDARD, 3, 3, 3)
    ns = [nurse(1)]
    cells = generate(Roster(OCT, ns, {}, rules, 0))
    assert "COVERAGE" in ids(validate(Roster(OCT, ns, cells, rules, 0)))


def test_generator_keeps_fixed_cells_and_counts_them_toward_coverage():
    ns = [nurse(i) for i in range(1, 13)]
    rules = Rules.preset(Preset.STANDARD, 2, 2, 2)
    cells = generate(Roster(OCT, ns, {1: {START: Duty.N}}, rules, 0))
    assert cells[1][START] == Duty.N
    assert sum(1 for n in ns if cells[n.id].get(START) == Duty.N) == 2, "고정 셀 포함 필요 인원만큼만 배정"


def test_consecutive_rules_span_month_boundary():
    """리뷰 #1: 9/29~10/3 연속 야간 5일은 월이 바뀌어도 위반이어야 한다"""
    rules = Rules.preset(Preset.STANDARD, 0, 0, 0)  # 연속 야간 최대 3, N→D 금지
    ns = [nurse(1), nurse(2)]
    before = {1: {day(-2): Duty.N, day(-1): Duty.N}, 2: {day(-1): Duty.N}}
    cells = {1: {START: Duty.N, day(1): Duty.N, day(2): Duty.N}, 2: {START: Duty.D}}
    vs = validate(Roster(OCT, ns, cells, rules, 0, before=before))
    assert [v.date for v in vs if v.rule_id == "MAX_CONSECUTIVE_NIGHTS"] == [day(1), day(2)]
    assert any(v.rule_id == "NIGHT_TO_DAY" and v.nurse_id == 2 and v.date == START for v in vs)
    assert all(v.date is None or v.date >= START for v in vs), "이전 달 날짜는 위반으로 보고하지 않음"


def test_generator_respects_previous_month_streak():
    rules = Rules.preset(Preset.STANDARD, 0, 0, 1)
    ns = [nurse(1), nurse(2)]
    before = {1: {day(-3): Duty.N, day(-2): Duty.N, day(-1): Duty.N}}
    cells = generate(Roster(OCT, ns, {}, rules, 0, before=before))
    assert cells[1].get(START) != Duty.N, "전달 말 N 3연속이면 1일 N 불가"


def test_wish_duty_is_preferred_and_checked():
    """리뷰 #9: 희망 근무는 생성에 반영되고, 미반영 시 소프트 위반"""
    rules = Rules.preset(Preset.STANDARD, 1, 0, 0)
    ns = [nurse(1), nurse(2)]
    wish = {2: {START: Duty.D}}
    cells = generate(Roster(OCT, ns, {}, rules, 0, wish_duties=wish))
    assert cells[2][START] == Duty.D, "희망한 사람이 먼저 배정"
    ignored = {1: {START: Duty.D}, 2: {START: Duty.O}}
    vs = validate(Roster(OCT, ns, ignored, rules, 0, wish_duties=wish))
    assert any(v.rule_id == "WISH_DUTY_IGNORED" and v.severity == Severity.SOFT for v in vs)
