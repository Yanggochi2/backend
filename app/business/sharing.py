"""공유 토큰은 확정된 한 달의 근무표에 대한 조회 권한만 제공한다."""
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business import audit, members, nurses, schedules
from app.business.common import conflict, not_found, sha256
from app.domain.model import Duty, DutyRole
from app.domain.nurse_sort import sorted_nurses
from app.persistence.models import Schedule, ScheduleShare


def issue(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID) -> dict:
    m = members.require_head(db, user_id)
    schedule = schedules.find(db, m, schedule_id)
    db.refresh(schedule, with_for_update=True)
    if schedule.status not in schedules.VISIBLE_TO_NURSES:
        raise conflict("SCHEDULE_NOT_CONFIRMED", "확정된 근무표만 공유할 수 있습니다")
    token = secrets.token_urlsafe(32)
    share = db.scalar(select(ScheduleShare).where(ScheduleShare.schedule_id == schedule_id))
    if share is None:
        share = ScheduleShare(schedule_id=schedule_id, token_hash=sha256(token))
        db.add(share)
    else:
        share.token_hash = sha256(token)
    db.flush()
    audit.log(db, m.ward_id, user_id, "SCHEDULE_SHARE_ISSUED", "SCHEDULE", schedule_id)
    return {"shareToken": token, "sharePath": f"/shared/{token}", "scheduleId": schedule_id}


def revoke(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID) -> None:
    m = members.require_head(db, user_id)
    schedule = schedules.find(db, m, schedule_id)
    db.refresh(schedule, with_for_update=True)
    share = db.scalar(select(ScheduleShare).where(ScheduleShare.schedule_id == schedule_id))
    if share is not None:
        db.delete(share)
        audit.log(db, m.ward_id, user_id, "SCHEDULE_SHARE_REVOKED", "SCHEDULE", schedule_id)


def get(db: Session, token: str, sort: str | None) -> dict:
    order = nurses.normalize_sort(sort)
    share = db.scalar(select(ScheduleShare).where(ScheduleShare.token_hash == sha256(token)))
    schedule = db.get(Schedule, share.schedule_id) if share else None
    if schedule is None or schedule.status not in schedules.VISIBLE_TO_NURSES:
        raise not_found("SCHEDULE_NOT_FOUND")
    roster = schedules.roster(db, schedule)
    rows = sorted_nurses(roster.nurses, order)
    # 화이트리스트 응답. 상태/경력/입사연도/연차 사유/계정/잠금/규칙은 노출하지 않는다.
    # AL/ED도 공개 화면에서는 부재(O)로 통일한다.
    return {"yearMonth": schedule.year_month, "status": schedule.status, "version": schedule.version,
            "defaultView": "month", "defaultSort": "proficiency", "sort": order,
            "nurses": [{"id": n.id, "name": n.name, "role": n.role,
                        "dutyRole": DutyRole.GENERAL if n.duty_role == DutyRole.PRECEPTOR else n.duty_role} for n in rows],
            "cells": [{"nurseId": n.id, "date": d.isoformat(),
                       "dutyCode": Duty.O if roster.cell(n.id, d) in (Duty.AL, Duty.ED) else roster.cell(n.id, d),
                       "editable": False} for n in rows for d in roster.days()]}
