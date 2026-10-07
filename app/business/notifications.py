"""
NOTI-01. 앱 내 알림함으로 저장한다.
ponytail: 웹 푸시(🔶)는 수단 확정 후 notify_user 한 곳에 발송만 붙이면 된다 (Yanggochi2/backend#27)
"""
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.business.common import ApiException, iso, not_found, page, today_kst
from app.domain.model import NotificationType, NurseStatus, ScheduleStatus
from app.persistence.db import now
from app.persistence.models import Assignment, Notification, Nurse, Schedule, User

# 설정으로 끌 수 있는 알림 종류. 나머지(가입 결과·잠금 인수)는 항상 받는다
SETTING_OF = {NotificationType.SCHEDULE_CONFIRMED: "scheduleConfirmed",
              NotificationType.SCHEDULE_CANCELLED: "scheduleCancelled",
              NotificationType.REQUEST_RESULT: "requestResult",
              NotificationType.DUTY_REMINDER: "dutyReminder"}
DEFAULT_SETTINGS = {"scheduleConfirmed": True, "scheduleCancelled": True, "requestResult": True, "dutyReminder": True,
                    "webPush": False}


def notify_user(db: Session, user_id: uuid.UUID | None, type_: NotificationType, title: str, body: str,
                resource_type: str | None = None, resource_id=None) -> None:
    if user_id is None:
        return
    u = db.get(User, user_id)
    key = SETTING_OF.get(type_)
    if u is None or (key and not _settings(u)[key]):
        return
    db.add(Notification(user_id=user_id, type=type_, title=title, body=body, resource_type=resource_type,
                        resource_id=None if resource_id is None else str(resource_id)))


def notify_ward(db: Session, ward_id: uuid.UUID, type_: NotificationType, title: str, body: str,
                resource_type: str | None = None, resource_id=None) -> None:
    """병동의 계정 있는 재직 간호사 전원"""
    for n in db.scalars(select(Nurse).where(Nurse.ward_id == ward_id, Nurse.user_id.is_not(None),
                                            Nurse.status != NurseStatus.RETIRED)):
        notify_user(db, n.user_id, type_, title, body, resource_type, resource_id)


def list_(db: Session, user_id: uuid.UUID, unread_only: bool, type_: NotificationType | None, page_no: int,
          size: int) -> dict:
    q = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    if type_:
        q = q.where(Notification.type == type_)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(Notification.created_at.desc(), Notification.id)
                      .offset(page_no * size).limit(size)).all()
    unread = db.scalar(select(func.count()).where(Notification.user_id == user_id, Notification.read_at.is_(None)))
    return page([_view(n) for n in rows], page_no, size, total, unreadCount=unread)


def mark(db: Session, user_id: uuid.UUID, notification_id: uuid.UUID, read: bool) -> dict:
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user_id:
        raise not_found("NOTIFICATION_NOT_FOUND")
    n.read_at = (n.read_at or now()) if read else None
    return _view(n)


def settings(db: Session, user_id: uuid.UUID) -> dict:
    return _settings(db.get(User, user_id))


def update_settings(db: Session, user_id: uuid.UUID, changes: dict) -> dict:
    if changes.get("webPush"):
        raise ApiException(422, "PUSH_NOT_SUPPORTED", "웹 푸시는 아직 지원하지 않습니다")
    u = db.get(User, user_id)
    u.notification_settings = {**_settings(u), **changes}
    return _settings(u)


def remind_tomorrow(db: Session) -> None:
    """근무 전날 리마인드. 확정본 기준"""
    tomorrow: date = today_kst() + timedelta(days=1)
    for s in db.scalars(select(Schedule).where(Schedule.year_month == tomorrow.strftime("%Y-%m"),
                                               Schedule.status == ScheduleStatus.CONFIRMED)):
        for a in db.scalars(select(Assignment).where(Assignment.schedule_id == s.id, Assignment.date == tomorrow)):
            if a.duty.is_work:
                n = db.get(Nurse, a.nurse_id)
                notify_user(db, n.user_id, NotificationType.DUTY_REMINDER, "내일 근무",
                            f"내일({tomorrow}) {a.duty} 근무입니다", "SCHEDULE", s.id)


def _settings(u: User) -> dict:
    return {**DEFAULT_SETTINGS, **(u.notification_settings or {})}


def _view(n: Notification) -> dict:
    return {"id": n.id, "type": n.type, "title": n.title, "body": n.body, "read": n.read_at is not None,
            "createdAt": iso(n.created_at), "resourceType": n.resource_type, "resourceId": n.resource_id}
