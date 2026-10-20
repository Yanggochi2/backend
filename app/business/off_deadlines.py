"""월별 희망 오프 신청 마감 조회·설정·접수 시 검증."""
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business import audit, members
from app.business.common import conflict, iso
from app.persistence.db import now
from app.persistence.models import OffRequestDeadline, Ward


def lock_ward(db: Session, ward_id: uuid.UUID) -> None:
    # 아직 월별 마감 행이 없어도 설정과 신청을 같은 병동 잠금으로 직렬화한다.
    db.get(Ward, ward_id, with_for_update=True, populate_existing=True)


def _row(db: Session, ward_id: uuid.UUID, ym: str):
    return db.scalar(select(OffRequestDeadline).where(OffRequestDeadline.ward_id == ward_id,
                                                     OffRequestDeadline.year_month == ym))


def view(row: OffRequestDeadline | None, ym: str) -> dict:
    return {"yearMonth": ym, "closesAt": iso(row.closes_at) if row else None,
            "manuallyClosed": row.manually_closed if row else False,
            "isClosed": bool(row and (row.manually_closed or (row.closes_at is not None and now() >= row.closes_at))),
            "version": row.version if row else 0}


def get(db: Session, user_id: uuid.UUID, ym: str) -> dict:
    m = members.require(db, user_id)
    return view(_row(db, m.ward_id, ym), ym)


def put(db: Session, user_id: uuid.UUID, ym: str, closes_at: datetime | None,
        manually_closed: bool, version: int) -> dict:
    m = members.require_head(db, user_id)
    lock_ward(db, m.ward_id)
    row = _row(db, m.ward_id, ym)
    before = view(row, ym)
    if before["version"] != version:
        raise conflict("VERSION_CONFLICT", "마감 설정이 변경되었습니다", {"currentVersion": before["version"]})
    if row is None:
        row = OffRequestDeadline(ward_id=m.ward_id, year_month=ym)
        db.add(row)
    row.closes_at = closes_at.astimezone(UTC).replace(tzinfo=None) if closes_at is not None else None
    row.manually_closed = manually_closed
    db.flush()
    result = view(row, ym)
    audit.log(db, m.ward_id, user_id, "OFF_DEADLINE_CHANGED", "OFF_REQUEST_DEADLINE", row.id, before, result)
    return result


def require_open(db: Session, ward_id: uuid.UUID, ym: str) -> None:
    # 호출자는 같은 트랜잭션에서 병동 잠금을 먼저 획득한다.
    if view(_row(db, ward_id, ym), ym)["isClosed"]:
        raise conflict("OFF_REQUEST_CLOSED", "이 달의 희망 오프 신청이 마감되었습니다")
