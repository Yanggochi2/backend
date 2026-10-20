"""
NOTI-01. 앱 내 알림함으로 저장한다.
ponytail: 웹 푸시(🔶)는 수단 확정 후 notify_user 한 곳에 발송만 붙이면 된다 (Yanggochi2/backend#27)
"""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.model import NotificationType, NurseStatus
from app.persistence.models import Notification, Nurse, User

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


def _settings(u: User) -> dict:
    return {**DEFAULT_SETTINGS, **(u.notification_settings or {})}
