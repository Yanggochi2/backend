import uuid

from sqlalchemy import select

from conftest import Api, YM, add_nurses, confirm_schedule, err, fill_rotation, ward_head
from app.persistence.db import SessionLocal
from app.persistence.models import ScheduleShare


def confirmed():
    h = ward_head()
    add_nurses(h, 5)
    s = fill_rotation(h, h.data("POST", "/wards/me/schedules", {"yearMonth": YM}))
    return h, confirm_schedule(h, s)


def test_share_is_read_only_and_minimizes_personal_data():
    h, s = confirmed()
    link = h.data("POST", f"/wards/me/schedules/{s['id']}/share-link")
    public = Api()
    response = public("GET", f"/shared/schedules/{link['shareToken']}")
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    data = response.json()['data']
    assert data['defaultView'] == 'month' and data['defaultSort'] == 'proficiency'
    assert data['nurses'][0]['role'] == 'HEAD_NURSE'
    assert set(data['nurses'][0]) == {'id', 'name', 'role', 'dutyRole'}
    assert not any(c['editable'] for c in data['cells'])
    assert 'statistics' not in data and 'readiness' not in data and 'lock' not in data
    public.c.headers['Authorization'] = 'Bearer ' + link['shareToken']
    err(public('GET', '/wards/me/nurses'), 401, 'UNAUTHENTICATED')
    err(public('POST', '/wards/me/schedules', {'yearMonth': '2028-01'}), 403, 'CSRF_TOKEN_INVALID')
    assert public('PATCH', f"/shared/schedules/{link['shareToken']}", {}).status_code == 405
    by_name = public.data('GET', f"/shared/schedules/{link['shareToken']}?sort=name")
    assert [n['name'] for n in by_name['nurses']] == sorted(n['name'] for n in by_name['nurses'])
    with SessionLocal() as db:
        share = db.scalar(select(ScheduleShare).where(ScheduleShare.schedule_id == uuid.UUID(s['id'])))
        assert len(share.token_hash) == 64
        assert share.token_hash != link['shareToken']


def test_share_rotation_revocation_and_other_ward():
    h, s = confirmed()
    path = f"/wards/me/schedules/{s['id']}/share-link"
    old = h.data('POST', path)['shareToken']
    current = h.data('POST', path)['shareToken']
    public = Api()
    err(public('GET', f'/shared/schedules/{old}'), 404, 'SCHEDULE_NOT_FOUND')
    assert public('GET', f'/shared/schedules/{current}').status_code == 200
    err(ward_head()('DELETE', path), 404, 'SCHEDULE_NOT_FOUND')
    assert h('DELETE', path).status_code == 204
    err(public('GET', f'/shared/schedules/{current}'), 404, 'SCHEDULE_NOT_FOUND')


def test_draft_is_hidden_and_unconfirm_permanently_revokes_link():
    h = ward_head()
    add_nurses(h, 5)
    s = h.data('POST', '/wards/me/schedules', {'yearMonth': YM})
    path = f"/wards/me/schedules/{s['id']}"
    err(h('POST', path + '/share-link'), 409, 'SCHEDULE_NOT_CONFIRMED')
    s = confirm_schedule(h, fill_rotation(h, s))
    token = h.data('POST', path + '/share-link')['shareToken']
    h.data('POST', path + '/confirmation-cancellations', {'reason': '수정'})
    err(Api()('GET', f'/shared/schedules/{token}'), 404, 'SCHEDULE_NOT_FOUND')
    confirm_schedule(h, s)
    err(Api()('GET', f'/shared/schedules/{token}'), 404, 'SCHEDULE_NOT_FOUND')


def test_secret_issuance_is_not_cached_in_plaintext():
    from app.persistence.models import IdempotencyRecord
    h, s = confirmed()
    token = h.data('POST', f"/wards/me/schedules/{s['id']}/share-link",
                   headers={'Idempotency-Key': 'secret-share'})['shareToken']
    with SessionLocal() as db:
        assert not list(db.scalars(select(IdempotencyRecord).where(IdempotencyRecord.key == 'secret-share')))
    assert Api()('GET', f'/shared/schedules/{token}').status_code == 200


def test_manager_schedule_sort_defaults_and_name_option():
    h = ward_head()
    add_nurses(h, 1, prefix='가나다')
    s = h.data('POST', '/wards/me/schedules', {'yearMonth': YM})
    assert s['nurses'][0]['role'] == 'HEAD_NURSE'
    path = f"/wards/me/schedules/{s['id']}"
    assert h.data('GET', path + '?sort=name')['nurses'][0]['name'] == '가나다00'
    assert h.data('GET', f'/wards/me/schedules?yearMonth={YM}&sort=name')['nurses'][0]['name'] == '가나다00'
    err(h('GET', path + '?sort=skillLevel'), 400, 'INVALID_SORT')
