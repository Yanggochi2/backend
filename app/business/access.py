"""로그인한 수간호사 계정의 초기 병동 개설."""
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business import audit, members, wards
from app.business.common import conflict, not_found, unprocessable
from app.domain.model import AccountStatus, NurseStatus, Role
from app.persistence.models import Nurse, User, Ward


def create(db: Session, user_id: uuid.UUID, fields: dict) -> dict:
    """로그인한 계정을 새 병동의 수간호사로 등록한다."""
    actor = db.get(User, user_id)
    actor.name = fields["head_name"]
    result = wards.create(db, user_id, fields["hospital_name"], fields["ward_name"],
                          fields["required_staff"], fields["rule_preset"])
    head = members.membership(db, user_id)
    head.name = fields["head_name"]
    head.joined_at = date(fields["joined_year"], 1, 1)
    head.affiliation_start = fields["affiliation_start"]
    head.career_months = fields["career_months"]
    db.flush()
    return {"ward": result["ward"], "headNurseId": head.id}


def associate_head_account(db: Session, ward_id: uuid.UUID, email: str) -> dict:
    """기존 병동의 합성 사용자를 실제 수간호사 계정으로 교체하는 운영자 전용 단계."""
    if db.get(Ward, ward_id) is None:
        raise not_found("WARD_NOT_FOUND")
    account = db.scalar(select(User).where(User.email == email.strip().lower()))
    if account is None:
        raise not_found("ACCOUNT_NOT_FOUND")
    real_password = account.password_hash.startswith(("$2a$", "$2b$", "$2y$"))
    if account.status != AccountStatus.ACTIVE or not real_password or account.email.endswith("@workspace.invalid"):
        raise unprocessable("ACCOUNT_NOT_ELIGIBLE", "활성 상태의 실제 로그인 계정만 연결할 수 있습니다")

    current = members.membership(db, account.id)
    if current is not None:
        if current.ward_id == ward_id and current.role == Role.HEAD_NURSE:
            return {"wardId": ward_id, "headNurseId": current.id, "accountId": account.id,
                    "email": account.email, "alreadyLinked": True}
        raise conflict("ACCOUNT_ALREADY_ASSOCIATED", "이미 다른 재직 소속과 연결된 계정입니다")

    heads = list(db.scalars(select(Nurse).where(
        Nurse.ward_id == ward_id, Nurse.role == Role.HEAD_NURSE, Nurse.status != NurseStatus.RETIRED)))
    candidates = []
    for head in heads:
        linked = db.get(User, head.user_id) if head.user_id else None
        if linked is None or (linked.password_hash == "!" and linked.email.endswith("@workspace.invalid")):
            candidates.append(head)
    if len(candidates) != 1:
        raise conflict("HEAD_ACCOUNT_ASSOCIATION_AMBIGUOUS",
                       "연결 가능한 기존 수간호사 행이 정확히 한 명이어야 합니다")

    head = candidates[0]
    before = head.user_id
    head.user_id = account.id
    db.flush()
    audit.log(db, ward_id, account.id, "HEAD_ACCOUNT_ASSOCIATED", "NURSE", head.id, before, account.id)
    return {"wardId": ward_id, "headNurseId": head.id, "accountId": account.id,
            "email": account.email, "alreadyLinked": False}
