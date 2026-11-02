"""로그인한 수간호사 계정의 초기 병동 개설."""
import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.business import members, wards
from app.persistence.models import User


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
