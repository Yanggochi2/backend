import uuid
from datetime import datetime
from typing import Annotated

from sqlalchemy import JSON, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.model import (AccountStatus)
from app.persistence.db import Base, now

# 모든 식별자는 UUID. 순번 id로 다른 병동 리소스를 추측하지 못하게 한다
PK = Annotated[uuid.UUID, mapped_column(primary_key=True, default=uuid.uuid4)]


def _enum(e):
    return Enum(e, native_enum=False, length=20)


class User(Base):
    __tablename__ = "users"
    id: Mapped[PK]
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[AccountStatus] = mapped_column(_enum(AccountStatus), default=AccountStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(default=now)
    # NOTI-01 알림 설정 (없는 키는 기본값)
    notification_settings: Mapped[dict] = mapped_column(JSON, default=dict)


class SessionToken(Base):
    """액세스·리프레시 토큰 쌍. DB에는 해시만 저장한다"""
    __tablename__ = "sessions"
    id: Mapped[PK]
    access_hash: Mapped[str] = mapped_column(String(64), unique=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    access_expires_at: Mapped[datetime]
    refresh_expires_at: Mapped[datetime]


class AuditLog(Base):
    """SEC-03. 조회 전용. 수정·삭제 경로 없음"""
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_ward_at", "ward_id", "occurred_at"),)
    id: Mapped[PK]
    ward_id: Mapped[uuid.UUID | None]
    actor_id: Mapped[uuid.UUID | None]
    action_type: Mapped[str] = mapped_column(String(50))
    target_type: Mapped[str | None] = mapped_column(String(30))
    target_id: Mapped[str | None] = mapped_column(String(64))
    before_value: Mapped[str | None] = mapped_column(String(2000))
    after_value: Mapped[str | None] = mapped_column(String(2000))
    occurred_at: Mapped[datetime] = mapped_column(default=now)


class IdempotencyRecord(Base):
    """1.5 Idempotency-Key. 같은 사용자·키·요청이면 저장된 응답을 그대로 돌려준다"""
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("user_id", "key"),)
    id: Mapped[PK]
    user_id: Mapped[uuid.UUID]
    key: Mapped[str] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int]
    body: Mapped[bytes]
    created_at: Mapped[datetime] = mapped_column(default=now)
