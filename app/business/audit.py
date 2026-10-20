"""SEC-03 감사 로그. 비밀번호·토큰·업로드 파일 원본·민감한 자유 입력은 남기지 않는다 (7장)"""
import uuid

from sqlalchemy.orm import Session

from app.persistence.models import AuditLog


def log(db: Session, ward_id: uuid.UUID | None, actor: uuid.UUID | None, action: str, target_type: str | None,
        target_id=None, before=None, after=None) -> None:
    db.add(AuditLog(ward_id=ward_id, actor_id=actor, action_type=action, target_type=target_type,
                    target_id=None if target_id is None else str(target_id),
                    before_value=None if before is None else str(before)[:2000],
                    after_value=None if after is None else str(after)[:2000]))
