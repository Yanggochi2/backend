from collections import Counter
from dataclasses import replace
from datetime import date, timedelta

from sqlalchemy import create_engine, select, text

from app.domain.generator import generate
from app.domain.model import Duty, DutyRole, NurseInfo, NurseStatus, Preset, Role, Roster, Rules, Severity
from app.domain.validator import off_target, validate
from app.persistence.db import SessionLocal
from app.persistence.migrations import upgrade
from app.persistence.models import Nurse, Ward
from app.seed import GENERAL, seed
from conftest import YM, add_nurses, err, ward_head


def test_off_formula_never_expands_to_fill_staffing_and_holidays_deduplicate():
    h = ward_head(1, 1, 1)
    add_nurses(h, 27)
    h.data('POST', '/wards/me/schedules', {'yearMonth': '2026-11'})
    path = '/wards/me/off-targets/2026-11'
    assert h.data('GET', path)['autoCount'] == 10  # 토요일4 + 일요일5 + 1
    h.data('PUT', '/wards/me/holidays/2026-11-07', {'isHoliday': True, 'name': '주말 중복'})
    assert h.data('GET', path)['autoCount'] == 10
    h.data('PUT', '/wards/me/holidays/2026-11-09', {'isHoliday': True, 'name': '평일 휴일'})
    assert h.data('GET', path)['autoCount'] == 11
    assert h('DELETE', '/wards/me/holidays/2026-11-09').status_code == 204
    assert h.data('GET', path)['autoCount'] == 10


def test_seed_has_exactly_28_nurses_and_is_repeatable():
    with SessionLocal() as db:
        result = seed(db)
        again = seed(db)
        assert result['ward']['id'] == again['ward']['id']
        rows = list(db.scalars(select(Nurse).where(Nurse.ward_id == result['ward']['id'])))
        assert len(rows) == 28
        assert sum(n.role == Role.HEAD_NURSE for n in rows) == 1
        assert sum(n.duty_role == DutyRole.CHARGE for n in rows) == 2
        assert sum(n.role != Role.HEAD_NURSE and n.duty_role != DutyRole.CHARGE for n in rows) == 25
        assert all(60 <= n.career_months <= 72 for n in rows if n.is_preceptor)
        assert any(n.career_months == 300 and n.duty_role == DutyRole.GENERAL for n in rows)
        assert any(n.career_months == 276 and n.duty_role == DutyRole.CHARGE for n in rows)
        db.rollback()


def test_existing_database_upgrade_preserves_data_and_is_repeatable(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path}/old.db')
    with engine.begin() as db:
        db.execute(text('CREATE TABLE nurses (id INTEGER PRIMARY KEY, duty_role VARCHAR(20), joined_at DATE)'))
        db.execute(text("INSERT INTO nurses VALUES (1, 'PRECEPTOR', '2020-07-15')"))
    upgrade(engine)
    upgrade(engine)
    with engine.connect() as db:
        assert db.execute(text('SELECT joined_at, is_preceptor FROM nurses')).one() == ('2020-07-15', 1)


def test_28_person_roster_matches_meeting_rules():
    month = date(2026, 11, 1)
    ns = [NurseInfo(0, '수간호사', DutyRole.GENERAL, NurseStatus.ACTIVE, date(2020, 1, 1), role=Role.HEAD_NURSE)]
    ns += [NurseInfo(i, f'차지{i}', DutyRole.CHARGE, NurseStatus.ACTIVE, date(2020, 1, 1)) for i in (1, 2)]
    ns += [NurseInfo(i + 3, name, DutyRole.NEW if career < 12 else DutyRole.GENERAL, NurseStatus.ACTIVE,
                     date(2020, 1, 1), career_months=career, is_preceptor=pre) for i, (name, career, pre) in enumerate(GENERAL)]
    holiday = date(2026, 11, 9)
    r = Roster(month, ns, {4: {date(2026, 11, 12): Duty.AL}, 5: {date(2026, 11, 13): Duty.ED}},
               Rules.preset(Preset.STANDARD, 7, 5, 4), 11, holidays=frozenset({holiday}))
    result = r.with_cells(generate(r, max_seconds=30))
    hard = [v for v in validate(result) if v.severity == Severity.HARD]
    assert not hard, hard[:5]
    counts = {n.id: Counter(result.cells[n.id].values()) for n in ns}
    for n in ns[:3]:
        for d in r.days():
            assert result.cell(n.id, d) == (Duty.O if d.weekday() >= 5 or d == holiday else Duty.D)
    assert all(counts[n.id][Duty.O] == 11 for n in ns[3:])
    assert result.cell(4, date(2026, 11, 12)) == Duty.AL
    assert result.cell(5, date(2026, 11, 13)) == Duty.ED
    for duty in (Duty.E, Duty.N):
        values = [counts[n.id][duty] for n in ns[3:]]
        assert max(values) - min(values) <= 2, (duty, values)
    assert sum(c[Duty.N] for c in counts.values()) == 4 * 30
    assert sum(c[Duty.E] for c in counts.values()) == 5 * 30


def test_fixed_conflicting_charge_and_unachievable_target_are_reported():
    n = NurseInfo(1, '차지', DutyRole.CHARGE, NurseStatus.ACTIVE, date(2020, 1, 1))
    day = date(2026, 11, 2)
    r = Roster(day.replace(day=1), [n], {1: {day: Duty.O}}, Rules.preset(Preset.STANDARD, 1, 0, 0), 10)
    out = r.with_cells(generate(r, max_seconds=.3))
    assert out.cell(1, day) == Duty.O
    assert any(v.rule_id == 'WEEKDAY_DAY_ONLY' and v.severity == Severity.HARD for v in validate(out))


def test_leave_education_do_not_replace_off_and_partial_affiliation_prorates():
    n = NurseInfo(1, '일반', DutyRole.GENERAL, NurseStatus.ACTIVE, date(2026, 11, 16))
    r = Roster(date(2026, 11, 1), [n], {}, Rules.preset(Preset.STANDARD, 0, 0, 0), 10)
    assert off_target(r, n) == 5
    cells = {1: {date(2026, 11, 16) + timedelta(days=i): Duty.AL for i in range(5)}}
    assert any(v.rule_id == 'OFF_TARGET' for v in validate(r.with_cells(cells)))


def test_seed_generation_confirmation_and_public_view():
    from app.business import members
    from app.persistence.models import Schedule
    from conftest import Api, confirm_schedule
    from app.business.common import today_kst
    with SessionLocal() as db:
        seeded = seed(db)
        schedule = db.scalar(select(Schedule).where(Schedule.ward_id == seeded['ward']['id']))
        schedule_id = str(schedule.id)
        db.commit()
    manager = Api()
    manager.c.headers['Authorization'] = 'Bearer ' + seeded['managementToken']
    path = f'/wards/me/schedules/{schedule_id}'
    job = manager.data('POST', path + '/generations', {'maxSeconds': 30})
    assert job['status'] == 'SUCCEEDED', job['conflicts'][:5]
    result = manager.data('GET', path)
    assert len(result['nurses']) == 28 and result['readiness']['hardViolations'] == 0
    result = confirm_schedule(manager, result)
    token = manager.data('POST', path + '/share-link')['shareToken']
    shared = Api().data('GET', f'/shared/schedules/{token}')
    assert shared['status'] == 'CONFIRMED' and len(shared['nurses']) == 28
    assert all(c['editable'] is False for c in shared['cells'])
