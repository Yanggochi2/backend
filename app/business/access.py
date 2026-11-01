"""로그인 없이 병동을 관리하는 편집용 capability. 일반 공유 토큰과 별개다."""
import secrets
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business import audit, members, wards
from app.business.common import ApiException, not_found, sha256
from app.persistence.models import Nurse, User, WardAccess


def issue(db: Session, ward_id: uuid.UUID) -> dict:
    head = db.scalar(select(Nurse).where(Nurse.ward_id == ward_id, Nurse.role == "HEAD_NURSE",
                                        Nurse.status != "RETIRED"))
    if head is None:
        raise not_found("HEAD_NURSE_NOT_FOUND")
    if head.user_id is None:
        actor = User(name=head.name, email=f"{uuid.uuid4()}@workspace.invalid", password_hash="!")
        db.add(actor)
        db.flush()
        head.user_id = actor.id
    token = secrets.token_urlsafe(32)
    grant = db.scalar(select(WardAccess).where(WardAccess.ward_id == ward_id).with_for_update())
    if grant is None:
        grant = WardAccess(ward_id=ward_id, actor_id=head.user_id, token_hash=sha256(token))
        db.add(grant)
    else:
        grant.actor_id, grant.token_hash = head.user_id, sha256(token)
    db.flush()
    audit.log(db, ward_id, head.user_id, "MANAGEMENT_LINK_ISSUED", "WARD", ward_id)
    return {"managementToken": token, "managementPath": f"/manage#token={token}"}


def resolve(db: Session, authorization: str | None) -> uuid.UUID:
    scheme, _, token = (authorization or "").partition(" ")
    grant = db.scalar(select(WardAccess).where(WardAccess.token_hash == sha256(token))) if scheme.lower() == "bearer" and token else None
    if grant is None:
        raise ApiException(401, "MANAGEMENT_LINK_REQUIRED", "유효한 편집 전용 링크가 필요합니다")
    member = members.require_head(db, grant.actor_id)
    if member.ward_id != grant.ward_id:
        raise ApiException(401, "MANAGEMENT_LINK_REQUIRED", "편집 링크의 병동 소속이 변경되었습니다")
    return grant.actor_id


def create(db: Session, fields: dict) -> dict:
    actor = User(name=fields["head_name"], email=f"{uuid.uuid4()}@workspace.invalid", password_hash="!")
    db.add(actor)
    db.flush()
    result = wards.create(db, actor.id, fields["hospital_name"], fields["ward_name"],
                          fields["required_staff"], fields["rule_preset"])
    head = members.membership(db, actor.id)
    head.joined_at = date(fields["joined_year"], 1, 1)
    head.affiliation_start = fields["affiliation_start"]
    head.career_months = fields["career_months"]
    db.flush()
    return {"ward": result["ward"], "headNurseId": head.id, **issue(db, head.ward_id)}


def rotate(db: Session, user_id: uuid.UUID) -> dict:
    return issue(db, members.require_head(db, user_id).ward_id)
