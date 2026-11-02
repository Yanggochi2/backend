"""규칙 설정 → 자동 생성 → 수동 수정 → 확정 → 근무표 확인 시나리오"""
from datetime import date, timedelta

from conftest import NEXT, YM, add_nurses, err, member, ward_head

from app.domain import generator, validator
from app.domain.model import Duty, DutyRole, NurseInfo, NurseStatus, Preset, Roster, Rules, Severity


def _nurses(k, **kw):
    return [NurseInfo(i, f"n{i}", kw.get("role", DutyRole.GENERAL), NurseStatus.ACTIVE, date(2025, 1, 1))
            for i in range(k)]


def _hard(r: Roster, cells) -> list[str]:
    return [v.rule_id for v in validator.validate(r.with_cells(cells)) if v.severity == Severity.HARD]


def test_generator_looks_ahead_of_fixed_cells():
    m = date(2026, 11, 1)
    fixed = {0: {m + timedelta(days=i): Duty.D for i in range(1, 6)}}  # 2~6일 D 고정 (최대 5일)
    r = Roster(m, _nurses(6), fixed, isolated_rules(Preset.STANDARD, 1, 1, 1), 0)
    cells = generator.generate(r)
    assert _hard(r, cells) == [] and cells[0][m] == Duty.O and cells[0][m + timedelta(days=6)] == Duty.O


def test_generator_never_puts_new_nurse_alone_on_night():
    m = date(2026, 11, 1)
    # 선배 1명은 연속 야간 3일 뒤 N에 못 들어간다 → 신입을 혼자 넣지 말고 인원 부족으로 남긴다
    ns = _nurses(1) + [NurseInfo(i, f"new{i}", DutyRole.NEW, NurseStatus.ACTIVE, date(2025, 1, 1)) for i in (1, 2, 3)]
    r = Roster(m, ns, {}, isolated_rules(Preset.STANDARD, 0, 0, 1), 0)
    hard = _hard(r, generator.generate(r))
    assert "NEW_NIGHT_ALONE" not in hard and "COVERAGE" in hard


def isolated_rules(*args):
    from dataclasses import replace
    rules = Rules.preset(*args)
    rules.settings["OFF_TARGET"] = replace(rules.settings["OFF_TARGET"], enabled=False)
    return rules
