import uuid

from conftest import YM, data, err, request
from app.domain.model import ScheduleStatus
from app.persistence.db import SessionLocal
from app.persistence.models import Schedule

def test_schedule_defaults_to_full_roster(client):
    schedule = data(client, "POST", "/wards/me/schedules", {"yearMonth": YM}, 201)
    assert len(schedule["nurses"]) == 28
    assert all({"rank", "hireYear", "isPreceptor"} <= set(n) for n in schedule["nurses"])

def test_confirmed_schedule_share_link_is_public_and_read_only(client):
    schedule = data(client, "POST", "/wards/me/schedules", {"yearMonth": YM}, 201)
    with SessionLocal() as db:
        row = db.get(Schedule, uuid.UUID(schedule["id"]))
        row.status = ScheduleStatus.CONFIRMED
        db.commit()
    shared = data(client, "POST", f"/wards/me/schedules/{schedule['id']}/share", {})
    public = data(client, "GET", f"/public/schedules/{shared['token']}")
    assert public["id"] == schedule["id"]
    assert public["status"] == "CONFIRMED"
    assert all(not cell["editable"] for cell in public["cells"])
    assert all("nightBlocked" not in nurse for nurse in public["nurses"])
    assert public["statistics"] == [] and "readiness" not in public
    rotated = data(client, "POST", f"/wards/me/schedules/{schedule['id']}/share", {})
    err(request(client, "GET", f"/public/schedules/{shared['token']}"), 404, "SHARED_SCHEDULE_NOT_FOUND")
    assert data(client, "GET", f"/public/schedules/{rotated['token']}")["id"] == schedule["id"]

def test_draft_schedule_cannot_be_shared(client):
    schedule = data(client, "POST", "/wards/me/schedules", {"yearMonth": YM}, 201)
    err(request(client, "POST", f"/wards/me/schedules/{schedule['id']}/share", {}), 409, "SCHEDULE_NOT_CONFIRMED")
