"""API-NOTI-01~04 알림"""
from conftest import YM, add_nurses, err, fill_rotation, member, ward_head


def test_notifications_and_settings():
    h = ward_head()
    add_nurses(h, 3)
    n, _ = member(h)
    s = fill_rotation(h, h.data("POST", "/wards/me/schedules", {"yearMonth": YM}))
    h.data("POST", f"/wards/me/schedules/{s['id']}/confirm", {})
    page = n("GET", "/me/notifications").json()
    types = [x["type"] for x in page["data"]]
    assert types == ["SCHEDULE_CONFIRMED", "MEMBERSHIP_APPROVED"] and page["meta"]["unreadCount"] == 2
    first = page["data"][0]
    assert first["resourceType"] == "SCHEDULE" and first["resourceId"] == s["id"] and first["title"]
    assert n.data("PATCH", f"/me/notifications/{first['id']}", {"read": True})["read"] is True
    assert [x["type"] for x in n.data("GET", "/me/notifications?unreadOnly=true")] == ["MEMBERSHIP_APPROVED"]
    err(h("PATCH", f"/me/notifications/{first['id']}", {"read": True}), 404, "NOTIFICATION_NOT_FOUND")

    assert n.data("GET", "/me/notification-settings") == {"scheduleConfirmed": True, "scheduleCancelled": True,
                                                         "requestResult": True, "dutyReminder": True, "webPush": False}
    err(n("PATCH", "/me/notification-settings", {"webPush": True}), 422, "PUSH_NOT_SUPPORTED")
    assert n.data("PATCH", "/me/notification-settings", {"scheduleCancelled": False})["scheduleCancelled"] is False
    h.data("POST", f"/wards/me/schedules/{s['id']}/confirmation-cancellations", {"reason": "수정"})
    assert "SCHEDULE_CANCELLED" not in [x["type"] for x in n.data("GET", "/me/notifications")], "끈 종류는 안 받음"


def test_remind_tomorrow_skips_nurses_without_account():
    import uuid
    from datetime import timedelta

    from app.business.common import today_kst
    from app.business.notifications import remind_tomorrow
    from app.domain.model import Duty, ScheduleStatus
    from app.persistence.db import SessionLocal
    from app.persistence.models import Assignment, Nurse, Schedule
    h = ward_head()
    (no_account,) = add_nurses(h, 1)
    head = uuid.UUID(h.data("GET", "/me")["membership"]["id"])
    tomorrow = today_kst() + timedelta(days=1)
    with SessionLocal() as db:
        s = Schedule(ward_id=db.get(Nurse, head).ward_id, year_month=tomorrow.strftime("%Y-%m"),
                     status=ScheduleStatus.CONFIRMED)
        db.add(s)
        db.flush()
        db.add_all([Assignment(schedule_id=s.id, nurse_id=head, date=tomorrow, duty=Duty.D),
                    Assignment(schedule_id=s.id, nurse_id=uuid.UUID(no_account), date=tomorrow, duty=Duty.E)])
        remind_tomorrow(db)
        db.commit()
    assert "DUTY_REMINDER" in [x["type"] for x in h.data("GET", "/me/notifications")]
