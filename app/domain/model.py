"""근무표 도메인 모델. 프레임워크(FastAPI·DB)에 의존하지 않는다 (CLAUDE.md 아키텍처 원칙)."""
import calendar
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum


class Duty(StrEnum):
    """COM-03. 셀의 None은 미배정이며 O와 구분한다."""
    D = "D"
    E = "E"
    N = "N"
    O = "O"
    AL = "AL"
    ED = "ED"

    @property
    def is_work(self) -> bool:
        return self in (Duty.D, Duty.E, Duty.N)


class Role(StrEnum):
    """COM-01 권한 역할. 계정이 아니라 병동 소속의 속성이다."""
    HEAD_NURSE = "HEAD_NURSE"
    NURSE = "NURSE"


class DutyRole(StrEnum):
    """NUR-05 배정 규칙용 속성. 권한(Role)과 별개."""
    CHARGE = "CHARGE"
    PRECEPTOR = "PRECEPTOR"
    NEW = "NEW"
    GENERAL = "GENERAL"
    NIGHT_ONLY = "NIGHT_ONLY"  # 나이트 전담: N과 OFF만, 월 나이트 15일 이내 (복지부 야간근무 가이드라인)


class NurseStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PREGNANT = "PREGNANT"
    ON_LEAVE = "ON_LEAVE"
    RETIRED = "RETIRED"


class ScheduleStatus(StrEnum):
    """COM-04"""
    DRAFT = "DRAFT"
    GENERATING = "GENERATING"
    CONFIRMED = "CONFIRMED"
    ARCHIVED = "ARCHIVED"


class RequestType(StrEnum):
    """REQ: 연차 / 희망 오프 / 희망 근무를 하나의 리소스로 통합"""
    ANNUAL_LEAVE = "ANNUAL_LEAVE"
    PREFERRED_OFF = "PREFERRED_OFF"
    PREFERRED_SHIFT = "PREFERRED_SHIFT"


class RequestStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    WITHDRAWN = "WITHDRAWN"  # 계정 탈퇴. 개인정보는 지우고 행만 남김


class GenerationStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    STOPPED = "STOPPED"
    NO_SOLUTION = "NO_SOLUTION"
    FAILED = "FAILED"


class Severity(StrEnum):
    HARD = "HARD"
    SOFT = "SOFT"


class NotificationType(StrEnum):
    """REQ-06"""
    SCHEDULE_CONFIRMED = "SCHEDULE_CONFIRMED"
    SCHEDULE_CANCELLED = "SCHEDULE_CANCELLED"
    REQUEST_RESULT = "REQUEST_RESULT"
    MEMBERSHIP_APPROVED = "MEMBERSHIP_APPROVED"
    MEMBERSHIP_REJECTED = "MEMBERSHIP_REJECTED"
    DUTY_REMINDER = "DUTY_REMINDER"
    LOCK_TAKEN_OVER = "LOCK_TAKEN_OVER"
    MEMBER_LEFT = "MEMBER_LEFT"
    MEMBER_JOINED = "MEMBER_JOINED"  # 초대 코드로 승인 없이 가입 → 수간호사에게 알림
    MEMBER_UNLINKED = "MEMBER_UNLINKED"  # 수간호사가 계정 연결을 해제 → 그 계정에게 알림


class Preset(StrEnum):
    """RULE-05"""
    STANDARD = "STANDARD"
    MINIMAL = "MINIMAL"


NIGHT_ONLY_MAX_NIGHTS = 15  # 나이트 전담 월 나이트 상한
SENIOR_ROLES = frozenset({DutyRole.CHARGE, DutyRole.PRECEPTOR})  # 근무마다 1명 이상 있어야 하는 고연차
# 규칙 코드 → 기본 심각도 (병동 설정이 없을 때). None은 기본 꺼짐. 근거는 docs/superpowers/specs/2026-10-26-cp-sat-generator-design.md
RULE_DEFAULTS = {
    "COVERAGE": Severity.HARD, "NIGHT_TO_DAY": Severity.HARD, "MAX_CONSECUTIVE_NIGHTS": Severity.HARD,
    "NIGHT_REST": Severity.HARD, "MAX_CONSECUTIVE_WORK": Severity.HARD, "PREGNANT_NIGHT": Severity.HARD,
    "OUT_OF_AFFILIATION": Severity.HARD, "CHARGE_DAY_ONLY": None, "NEW_NIGHT_ALONE": Severity.HARD,
    "CHARGE_EVERY_SHIFT": Severity.HARD, "SENIOR_EVERY_SHIFT": Severity.HARD, "NIGHT_ONLY_SHIFT": Severity.HARD,
    "SHIFT_BLOCK": Severity.SOFT,
    "WISH_OFF_IGNORED": Severity.SOFT, "MAX_MONTHLY_NIGHTS": Severity.SOFT, "OFF_TARGET": Severity.SOFT,
    "NIGHT_OFF_DAY": Severity.SOFT, "WISH_DUTY_IGNORED": Severity.SOFT, "WEEKLY_REST": Severity.SOFT,
    "ISOLATED_SHIFT": Severity.SOFT, "PRECEPTOR_MISMATCH": Severity.SOFT,
}


@dataclass(frozen=True)
class Rules:
    """RULE-01·02 값 + AUTH-00 듀티별 필요 인원"""
    required_d: int
    required_e: int
    required_n: int
    max_consecutive_nights: int
    max_consecutive_work_days: int
    forbid_night_to_day: bool
    max_monthly_nights: int = 6
    # RULE-01 규칙 코드 → 심각도. None이면 꺼짐, 없으면 RULE_DEFAULTS
    severity: dict = field(default_factory=dict, compare=False)

    def mode(self, code: str) -> "Severity | None":
        """규칙의 현재 심각도. 꺼졌으면 None"""
        return self.severity[code] if code in self.severity else RULE_DEFAULTS.get(code, Severity.HARD)

    def enforced(self, code: str) -> bool:
        """자동 생성이 반드시 지켜야 하는 규칙인지 (켜져 있고 HARD)"""
        return self.mode(code) == Severity.HARD

    @staticmethod
    def preset(p: Preset, d: int, e: int, n: int) -> "Rules":
        if p == Preset.STANDARD:
            return Rules(d, e, n, 3, 5, True)
        return Rules(d, e, n, 31, 31, False)

    def required(self, duty: Duty) -> int:
        return {Duty.D: self.required_d, Duty.E: self.required_e, Duty.N: self.required_n}.get(duty, 0)


@dataclass(frozen=True)
class Violation:
    """SCH-06 위반 단위. nurse_id가 None이면 병동 단위(커버리지) 위반."""
    severity: Severity
    rule_id: str
    nurse_id: object | None
    date: date | None
    current: Duty | None
    message: str

    @property
    def key(self) -> str:
        """확정 시 소프트 위반 확인(acknowledge)에 쓰는 안정적인 식별 문자열"""
        return f"{self.rule_id}|{self.nurse_id}|{self.date}|{self.current}"


@dataclass(frozen=True)
class NurseInfo:
    """규칙 검증·생성에 필요한 간호사 정보만 담은 순수 모델"""
    id: object  # UUID (테스트에서는 int)
    name: str
    duty_role: DutyRole
    status: NurseStatus
    affiliation_start: date
    affiliation_end: date | None = None
    preceptees: frozenset = frozenset()

    def affiliated(self, d: date) -> bool:
        return self.affiliation_start <= d and (self.affiliation_end is None or d <= self.affiliation_end)


Cells = dict[object, dict[date, Duty]]


@dataclass(frozen=True)
class Roster:
    """
    한 달 근무표의 검증·생성 입력.
    cells: 간호사 id -> 날짜 -> 듀티 (없으면 미배정)
    wish_offs / wish_duties: 승인된 희망 오프 / 희망 근무
    before: 이전 달 근무 (읽기 전용). 연속 야간·연속 근무·N→D를 월 경계 너머로 이어 세기 위함
    """
    month: date  # 그 달 1일
    nurses: list[NurseInfo]
    cells: Cells
    rules: Rules
    off_target: int
    wish_offs: dict[object, set[date]] = field(default_factory=dict)
    wish_duties: Cells = field(default_factory=dict)
    before: Cells = field(default_factory=dict)

    def days(self) -> list[date]:
        n = calendar.monthrange(self.month.year, self.month.month)[1]
        return [self.month.replace(day=i) for i in range(1, n + 1)]

    def cell(self, nurse_id: int, d: date) -> Duty | None:
        src = self.before if d < self.month else self.cells
        return src.get(nurse_id, {}).get(d)

    def wish_duty(self, nurse_id: int, d: date) -> Duty | None:
        return self.wish_duties.get(nurse_id, {}).get(d)

    def with_cells(self, cells: Cells) -> "Roster":
        return replace(self, cells=cells)
