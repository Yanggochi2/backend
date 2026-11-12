"""요청 본문 검증. JSON 필드는 camelCase, 파이썬 속성은 snake_case"""
import re
import uuid
from datetime import date
from typing import Annotated

from pydantic import (AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StrictBool, StrictInt,
                      StringConstraints, field_validator)
from pydantic.alias_generators import to_camel

from app.domain.model import Duty, DutyRole, NurseStatus, Preset, Severity, StaffType

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


def _password_value(value: str) -> str:
    if not value or value.isspace():
        raise ValueError("INVALID_PASSWORD")
    if len(value.encode("utf-8")) > 72:
        raise ValueError("PASSWORD_TOO_LONG")
    return value


Password = Annotated[str, AfterValidator(_password_value)]


class Body(BaseModel):
    # 공통 요청 필드. 간호사·병동 생성 폼은 추가 필드를 거절한다.
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Signup(Body):
    name: Name
    email: EmailStr
    password: Password
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
    password: Password


class Withdraw(Body):
    password: Password


class Reason(Body):
    # 알림 body String(500)에 "{병동명(≤100)} 가입이 반려되었습니다. 사유: "와 함께 들어가므로 300자로 제한
    reason: Annotated[str, StringConstraints(max_length=300)] | None = None


class NurseFields(Body):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    @field_validator("joined_year", check_fields=False)
    @classmethod
    def valid_year(cls, value):
        from app.business.common import today_kst
        if value is not None and not 1900 <= value <= today_kst().year:
            raise ValueError("OUT_OF_RANGE")
        return value


class NurseCreate(NurseFields):
    name: Name
    staff_type: StaffType = StaffType.NURSE
    duty_role: DutyRole
    status: NurseStatus
    joined_year: StrictInt
    is_preceptor: StrictBool = False
    career_months: int | None = Field(None, ge=0)
    skill_level: int | None = Field(None, ge=1, le=5)
    affiliation_start: date
    affiliation_end: date | None = None
    preceptor_of: list[uuid.UUID] | None = None


class NursePatch(NurseFields):
    """등록과 같은 필드의 부분 수정. role·wardId는 입력할 수 없다."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    name: Name | None = None
    staff_type: StaffType | None = None
    duty_role: DutyRole | None = None
    status: NurseStatus | None = None
    joined_year: StrictInt | None = None
    is_preceptor: StrictBool | None = None
    career_months: int | None = Field(None, ge=0)
    skill_level: int | None = Field(None, ge=1, le=5)
    affiliation_start: date | None = None
    affiliation_end: date | None = None
    preceptor_of: list[uuid.UUID] | None = None
    version: int | None = None


class Retire(Body):
    affiliation_end: date


class RulePatch(Body):
    enabled: bool | None = None
    severity: Severity | None = None
    parameters: dict | None = None
    reason: str | None = None
    version: int | None = None


class HolidayPut(Body):
    is_holiday: StrictBool
    name: str | None = Field(None, max_length=100)
    reason: str | None = Field(None, max_length=500)


class OffTargetPatch(Body):
    target_count: int | None
    reason: str | None = None


class ScheduleCreate(Body):
    year_month: str


class CellChange(Body):
    nurse_id: uuid.UUID
    date: date
    duty_code: Duty | None  # 키는 필수, null = 미배정


class CellBulkPatch(Body):
    base_version: int
    changes: list[CellChange] = Field(min_length=1)


class ForceOffCell(Body):
    nurse_id: uuid.UUID
    date: date


class ForceOffPatch(Body):
    base_version: int
    cells: list[ForceOffCell] = Field(min_length=1)
    reason: str | None = Field(None, max_length=500)


class Confirm(Body):
    acknowledged_soft_violation_ids: list[uuid.UUID] = []


class CellRef(Body):
    nurse_id: uuid.UUID
    date: date


class GenerationStart(Body):
    fixed_cells: list[CellRef] = []
    max_seconds: int | None = None


class Stop(Body):
    apply_best_result: bool = False


class Relaxations(Body):
    relaxation_ids: list[str] = Field(min_length=1)


class BaseVersion(Body):
    base_version: int


class NurseMapping(Body):
    row_number: int
    nurse_id: uuid.UUID | None


class DutyMapping(Body):
    raw: str
    duty_code: Duty | None


class ImportMappingPatch(Body):
    nurse_mappings: list[NurseMapping] | None = None
    duty_mappings: list[DutyMapping] | None = None


class ChargePut(Body):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    is_charge: StrictBool
    version: StrictInt = Field(ge=1)


class WorkspaceCreate(NurseFields):
    hospital_name: NonBlank
    ward_name: NonBlank
    head_name: Name
    joined_year: StrictInt
    affiliation_start: date
    career_months: StrictInt = Field(ge=0)
    required_staff: dict[str, int]
    rule_preset: Preset = Preset.STANDARD
