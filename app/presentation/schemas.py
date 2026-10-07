"""요청 본문 검증. JSON 필드는 camelCase, 파이썬 속성은 snake_case"""
import re
import uuid
from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator
from pydantic.alias_generators import to_camel

from app.domain.model import Duty, DutyRole, NurseStatus, Preset

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class Body(BaseModel):
    # 정의하지 않은 필드(role, wardId 등)는 무시한다 (AUTH-01 보안: 역할은 요청 본문에서 받지 않음)
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Signup(Body):
    name: Name
    email: EmailStr
    password: str
    terms_agreed: bool

    @field_validator("password")
    @classmethod
    def password_rule(cls, v: str) -> str:
        if len(v) < 8 or not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
            raise ValueError("WEAK_PASSWORD")
        return v

    @field_validator("terms_agreed")
    @classmethod
    def must_agree(cls, v: bool) -> bool:
        if not v:
            raise ValueError("TERMS_NOT_AGREED")
        return v


class Login(Body):
    email: NonBlank
    password: NonBlank


class CreateWard(Body):
    hospital_name: NonBlank
    ward_name: NonBlank
    required_staff: dict[str, int]
    rule_preset: Preset


class JoinRequest(Body):
    join_code: str


class ApproveMembership(Body):
    nurse_id: uuid.UUID | None = None  # 계정을 연결할 기존 간호사 (선택)


class Reason(Body):
    reason: str | None = None


class Transfer(Body):
    target_nurse_id: uuid.UUID


class NurseCreate(Body):
    name: Name
    duty_role: DutyRole
    status: NurseStatus
    joined_at: date
    career_months: int = Field(ge=0)
    skill_level: int = Field(ge=1, le=5)
    affiliation_start: date
    affiliation_end: date | None = None
    preceptor_of: list[uuid.UUID] | None = None


class NursePatch(Body):
    """부분 수정. role 등 정의하지 않은 필드는 400 (권한 변경은 AUTH-07로만)"""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    name: Name | None = None
    duty_role: DutyRole | None = None
    status: NurseStatus | None = None
    joined_at: date | None = None
    career_months: int | None = Field(None, ge=0)
    skill_level: int | None = Field(None, ge=1, le=5)
    affiliation_start: date | None = None
    affiliation_end: date | None = None
    preceptor_of: list[uuid.UUID] | None = None
    version: int | None = None


class Retire(Body):
    affiliation_end: date


class ScheduleCreate(Body):
    year_month: str


class CellChange(Body):
    nurse_id: uuid.UUID
    date: date
    duty_code: Duty | None  # 키는 필수, null = 미배정


class CellBulkPatch(Body):
    base_version: int
    changes: list[CellChange] = Field(min_length=1)


class Confirm(Body):
    acknowledged_soft_violation_ids: list[uuid.UUID] = []
