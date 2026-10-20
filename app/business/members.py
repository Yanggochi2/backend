import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business.common import forbidden, iso, not_found
from app.domain.model import NurseStatus, Role
from app.persistence.models import Nurse


@dataclass(frozen=True)
class Member:
    """세션 사용자의 병동 소속. 병동·역할은 항상 서버가 판정한다 (1.1·1.2)"""
    user_id: uuid.UUID
    ward_id: uuid.UUID
    nurse_id: uuid.UUID
    role: Role

    @property
    def is_head(self) -> bool:
        return self.role == Role.HEAD_NURSE


def membership(db: Session, user_id: uuid.UUID) -> Nurse | None:
    # ponytail: 다중 병동 소속 미허용(COM-01 🔶) 가정. 허용 시 요청에 병동 선택 추가
    return db.scalars(select(Nurse).where(Nurse.user_id == user_id, Nurse.status != NurseStatus.RETIRED)
                      .order_by(Nurse.affiliation_start)).first()


def require(db: Session, user_id: uuid.UUID) -> Member:
    n = membership(db, user_id)
    if n is None:
        raise not_found("WARD_NOT_FOUND")
    return Member(user_id, n.ward_id, n.id, n.role)


def require_head(db: Session, user_id: uuid.UUID) -> Member:
    m = require(db, user_id)
    if not m.is_head:
        raise forbidden("수간호사 권한이 필요합니다")
    return m


def view(n: Nurse) -> dict:
    """Membership"""
    return {"id": n.id, "wardId": n.ward_id, "userId": n.user_id, "role": n.role, "status": n.status,
            "joinedAt": iso(n.affiliation_start)}
