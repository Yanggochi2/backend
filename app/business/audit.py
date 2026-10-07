"""SEC-03 감사 로그. 비밀번호·토큰·업로드 파일 원본·민감한 자유 입력은 남기지 않는다 (7장)"""
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.business import members
from app.business.common import bad_request, iso, page
from app.persistence.models import AuditLog


def log(db: Session, ward_id: uuid.UUID | None, actor: uuid.UUID | None, action: str, target_type: str | None,
        target_id=None, before=None, after=None) -> None:
    db.add(AuditLog(ward_id=ward_id, actor_id=actor, action_type=action, target_type=target_type,
                    target_id=None if target_id is None else str(target_id),
                    before_value=None if before is None else str(before)[:2000],
                    after_value=None if after is None else str(after)[:2000]))


def search(db: Session, user_id: uuid.UUID, frm: datetime | None, to: datetime | None, actor: uuid.UUID | None,
           action: str | None, page_no: int, size: int) -> dict:
    m = members.require_head(db, user_id)
    if frm and to and frm > to:
        raise bad_request("INVALID_DATE_RANGE", "from이 to보다 늦습니다", "from")
    q = select(AuditLog).where(AuditLog.ward_id == m.ward_id)
    if frm:
        q = q.where(AuditLog.occurred_at >= frm)
    if to:
        q = q.where(AuditLog.occurred_at < to)
    if actor is not None:
        q = q.where(AuditLog.actor_id == actor)
    if action:
        q = q.where(AuditLog.action_type == action)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(AuditLog.occurred_at.desc(), AuditLog.id).offset(page_no * size).limit(size)).all()
    return page([{"id": r.id, "occurredAt": iso(r.occurred_at), "actorId": r.actor_id, "actionType": r.action_type,
                  "targetType": r.target_type, "targetId": r.target_id, "before": r.before_value,
                  "after": r.after_value} for r in rows], page_no, size, total)
