import uuid
from datetime import date, datetime
from typing import Annotated

from sqlalchemy import JSON, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.model import (AccountStatus, Duty, DutyRole, GenerationStatus, NotificationType, NurseInfo,
                              NurseStatus, RequestStatus, RequestType, Role, ScheduleStatus, Severity)
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


class Ward(Base):
    __tablename__ = "wards"
    id: Mapped[PK]
    hospital_name: Mapped[str] = mapped_column(String(100))
    ward_name: Mapped[str] = mapped_column(String(100))
    # AUTH-05. 재발급 시 덮어써서 기존 코드는 즉시 무효
    code: Mapped[str] = mapped_column(String(8), unique=True)
    code_issued_at: Mapped[datetime] = mapped_column(default=now)
    created_at: Mapped[datetime] = mapped_column(default=now)


class WardRule(Base):
    """RULE-01. 병동별 규칙 설정. 필요 인원은 COVERAGE 규칙의 parameters {D,E,N}"""
    __tablename__ = "ward_rules"
    __table_args__ = (UniqueConstraint("ward_id", "code"),)
    id: Mapped[PK]
    ward_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wards.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(default=True)
    severity: Mapped[Severity] = mapped_column(_enum(Severity))
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(default=0)
    __mapper_args__ = {"version_id_col": version}


class MembershipRequest(Base):
    """AUTH-04 가입 신청"""
    __tablename__ = "membership_requests"
    id: Mapped[PK]
    ward_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wards.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[RequestStatus] = mapped_column(_enum(RequestStatus), default=RequestStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(default=now)
    processed_at: Mapped[datetime | None]
    processed_by: Mapped[uuid.UUID | None]
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(default=0)
    __mapper_args__ = {"version_id_col": version}


class Nurse(Base):
    """
    간호사 행 = 병동 소속(Membership). user_id가 None이면 계정 없이 수간호사가 등록한 간호사.
    역할(role)은 병동 소속의 속성
    """
    __tablename__ = "nurses"
    id: Mapped[PK]
    ward_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wards.id"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[Role] = mapped_column(_enum(Role), default=Role.NURSE)
    duty_role: Mapped[DutyRole] = mapped_column(_enum(DutyRole), default=DutyRole.GENERAL)
    status: Mapped[NurseStatus] = mapped_column(_enum(NurseStatus), default=NurseStatus.ACTIVE)
    joined_at: Mapped[date]
    career_months: Mapped[int] = mapped_column(default=0)
    skill_level: Mapped[int] = mapped_column(default=1)
    affiliation_start: Mapped[date]
    affiliation_end: Mapped[date | None]
    preceptor_of: Mapped[list] = mapped_column(JSON, default=list)  # UUID 문자열
    version: Mapped[int] = mapped_column(default=0)
    __mapper_args__ = {"version_id_col": version}

    def info(self) -> NurseInfo:
        return NurseInfo(self.id, self.name, self.duty_role, self.status, self.affiliation_start,
                         self.affiliation_end, frozenset(uuid.UUID(x) for x in self.preceptor_of or []))


class Holiday(Base):
    """RULE-02 병동 공휴일 보정. 기본 공휴일을 덮어쓴다"""
    __tablename__ = "holidays"
    __table_args__ = (UniqueConstraint("ward_id", "date"),)
    id: Mapped[PK]
    ward_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wards.id"))
    date: Mapped[date]
    is_holiday: Mapped[bool]
    name: Mapped[str | None] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(String(500))


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (UniqueConstraint("ward_id", "year_month"),)
    id: Mapped[PK]
    ward_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wards.id"))
    year_month: Mapped[str] = mapped_column(String(7))  # yyyy-MM
    status: Mapped[ScheduleStatus] = mapped_column(_enum(ScheduleStatus), default=ScheduleStatus.DRAFT)
    # RULE-03 수동 조정값. None이면 자동 계산
    off_target: Mapped[int | None]
    off_target_reason: Mapped[str | None] = mapped_column(String(500))
    # SCH-07 확정·확정 취소 이력 (마지막 값)
    confirmed_at: Mapped[datetime | None]
    confirmed_by: Mapped[uuid.UUID | None]
    cancelled_at: Mapped[datetime | None]
    cancelled_by: Mapped[uuid.UUID | None]
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    confirm_count: Mapped[int] = mapped_column(default=0)
    # SCH-11 편집 잠금. 토큰은 해시만 저장
    locked_by: Mapped[uuid.UUID | None]
    locked_at: Mapped[datetime | None]
    lock_activity_at: Mapped[datetime | None]
    lock_token_hash: Mapped[str | None] = mapped_column(String(64))
    # 내용(셀·상태) 버전. 잠금 획득·해제로는 오르지 않는다. 조건부 UPDATE로 올린다 (schedules.touch)
    updated_at: Mapped[datetime] = mapped_column(default=now)
    version: Mapped[int] = mapped_column(default=1)


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("schedule_id", "nurse_id", "date"),)
    id: Mapped[PK]
    schedule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schedules.id"), index=True)
    nurse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nurses.id"))
    date: Mapped[date]
    duty: Mapped[Duty] = mapped_column(_enum(Duty))


class WorkRequest(Base):
    """REQ. 연차·희망오프·희망근무 통합 신청. 대상 날짜는 같은 달 안에서 여러 개"""
    __tablename__ = "work_requests"
    id: Mapped[PK]
    ward_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wards.id"), index=True)
    nurse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nurses.id"), index=True)
    type: Mapped[RequestType] = mapped_column(_enum(RequestType))
    year_month: Mapped[str] = mapped_column(String(7))
    target_dates: Mapped[list] = mapped_column(JSON)  # yyyy-MM-dd 문자열, 정렬됨
    reason_code: Mapped[str] = mapped_column(String(20))
    reason_detail: Mapped[str | None] = mapped_column(String(500))
    preferred_duty: Mapped[Duty | None] = mapped_column(_enum(Duty))
    status: Mapped[RequestStatus] = mapped_column(_enum(RequestStatus), default=RequestStatus.PENDING)
    processed_by: Mapped[uuid.UUID | None]
    processed_at: Mapped[datetime | None]
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(default=now)
    version: Mapped[int] = mapped_column(default=0)
    __mapper_args__ = {"version_id_col": version}

    def dates(self) -> list[date]:
        return [date.fromisoformat(d) for d in self.target_dates]


class GenerationJob(Base):
    """GEN-01·02·04"""
    __tablename__ = "generation_jobs"
    id: Mapped[PK]
    ward_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wards.id"), index=True)
    schedule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schedules.id"), index=True)
    status: Mapped[GenerationStatus] = mapped_column(_enum(GenerationStatus))
    stage: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(default=now)
    finished_at: Mapped[datetime | None]
    max_seconds: Mapped[int]
    fixed_cells: Mapped[list] = mapped_column(JSON, default=list)  # [[nurseId, date], ...]
    base_version: Mapped[int]  # 생성 시작 시점의 근무표 버전
    hard_violation_count: Mapped[int] = mapped_column(default=0)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    conflicts: Mapped[list] = mapped_column(JSON, default=list)
    relaxations: Mapped[list] = mapped_column(JSON, default=list)
    partial_cells: Mapped[list | None] = mapped_column(JSON)  # [[nurseId, date, duty], ...]
    created_by: Mapped[uuid.UUID]


class ImportPreview(Base):
    """SCH-10 엑셀 가져오기 미리보기. 매핑을 확정한 뒤 반영한다"""
    __tablename__ = "import_previews"
    id: Mapped[PK]
    ward_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wards.id"))
    schedule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schedules.id"), index=True)
    rows: Mapped[list] = mapped_column(JSON)
    cells: Mapped[list] = mapped_column(JSON)
    nurse_mappings: Mapped[dict] = mapped_column(JSON)  # rowNumber(str) → nurseId 문자열 또는 None(건너뜀)
    duty_mappings: Mapped[dict] = mapped_column(JSON)  # 엑셀 코드 → dutyCode 또는 None(미배정)
    applied_at: Mapped[datetime | None]
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(default=now)


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


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notification_user", "user_id", "created_at"),)
    id: Mapped[PK]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    type: Mapped[NotificationType] = mapped_column(_enum(NotificationType))
    title: Mapped[str] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(String(500))
    resource_type: Mapped[str | None] = mapped_column(String(30))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
    read_at: Mapped[datetime | None]


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
