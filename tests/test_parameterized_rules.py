from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.domain.generator import generate
from app.domain.model import Duty, DutyRole, NurseInfo, NurseStatus, Preset, Roster, Rules, Severity
from app.domain.rule_engine import can_assign
from app.domain.validator import validate

DAY = date(2027, 1, 1)


def nurse(i, role=DutyRole.GENERAL):
    return NurseInfo(i, str(i), role, NurseStatus.ACTIVE, DAY - timedelta(days=40))


def update(rules, code, **changes):
    return Rules({**rules.settings, code: replace(rules.settings[code], **changes)})


def test_max_parameter_changes_generator_and_validator():
    ns = [nurse(1), nurse(2)]
    base = Rules.preset(Preset.STANDARD, 0, 0, 1)
    restrictive = update(base, "MAX_CONSECUTIVE_NIGHTS", parameters={"max": 1})
    fixed = {1: {DAY: Duty.N}}
    r = Roster(DAY, ns, fixed, restrictive, 0)
    cells = generate(r)
    assert cells[1][DAY + timedelta(days=1)] != Duty.N
    invalid = r.with_cells({1: {DAY: Duty.N, DAY + timedelta(days=1): Duty.N}})
    assert any(v.rule_id == "MAX_CONSECUTIVE_NIGHTS" for v in validate(invalid))
    relaxed = replace(invalid, rules=update(base, "MAX_CONSECUTIVE_NIGHTS", parameters={"max": 2}))
    assert not any(v.rule_id == "MAX_CONSECUTIVE_NIGHTS" for v in validate(relaxed))
    assert can_assign(replace(r, rules=relaxed.rules), ns[0], Duty.N, DAY + timedelta(days=1))


def test_charge_parameter_and_disabled_or_soft_rule():
    # 기본은 D·E·N 모두 허용 → D만 허용으로 좁혀 검사
    base = update(Rules.preset(Preset.STANDARD, 0, 1, 0), "CHARGE_DAY_ONLY", enabled=True,
                  parameters={"allowedDuties": ["D"]})
    ns = [nurse(1, DutyRole.CHARGE)]
    r = Roster(DAY, ns, {}, base, 0)
    assert not can_assign(r, ns[0], Duty.E, DAY)
    for changes in ({"parameters": {"allowedDuties": ["D", "E"]}}, {"enabled": False}, {"severity": Severity.SOFT}):
        adjusted = replace(r, rules=update(base, "CHARGE_DAY_ONLY", **changes))
        assert can_assign(adjusted, ns[0], Duty.E, DAY)
        result = adjusted.with_cells(generate(adjusted))
        assert result.cell(1, DAY) == Duty.E
        assert not any(v.rule_id == "CHARGE_DAY_ONLY" and v.severity == Severity.HARD for v in validate(result))


def test_new_night_support_uses_current_assignments_and_parameter():
    ns = [nurse(1), nurse(2, DutyRole.NEW)]
    rules = Rules.preset(Preset.STANDARD, 0, 0, 2)
    roster = Roster(DAY, ns, {}, rules, 0)
    result = roster.with_cells(generate(roster))
    assert result.cell(1, DAY) == result.cell(2, DAY) == Duty.N
    strict = replace(roster, rules=update(rules, "NEW_NIGHT_ALONE", parameters={"minExperienced": 2}))
    result = strict.with_cells(generate(strict))
    assert result.cell(2, DAY) != Duty.N
    invalid = strict.with_cells({1: {DAY: Duty.N}, 2: {DAY: Duty.N}})
    assert any(v.rule_id == "NEW_NIGHT_ALONE" for v in validate(invalid))


def test_future_fixed_cell_and_previous_month_are_protected():
    n = nurse(1)
    rules = Rules.preset(Preset.STANDARD, 0, 0, 1)
    r = Roster(DAY, [n], {1: {DAY + timedelta(days=1): Duty.D}}, rules, 0)
    assert not can_assign(r, n, Duty.N, DAY)
    r = replace(r, cells={}, before={1: {DAY - timedelta(days=i): Duty.N for i in (1, 2, 3)}})
    assert not can_assign(r, n, Duty.N, DAY)


@pytest.mark.parametrize("code,params", [
    ("MAX_CONSECUTIVE_NIGHTS", {"max": True}), ("MAX_CONSECUTIVE_WORK", {"max": 32}),
    ("CHARGE_DAY_ONLY", {"allowedDuties": []}), ("CHARGE_DAY_ONLY", {"allowedDuties": ["D", "D"]}),
    ("NEW_NIGHT_ALONE", {"minExperienced": 0}), ("NIGHT_TO_DAY", {"unknown": 1}),
    ("COVERAGE", {"D": True, "E": 1, "N": 0}),
])
def test_invalid_parameter_contract(code, params):
    from app.domain.rule_catalog import parameters
    with pytest.raises(ValueError):
        parameters(code, params)
