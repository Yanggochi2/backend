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
    """자동 생성 내부 분류. 외부 입력은 NurseRank·숙련도·프리셉터 속성으로 받는다."""
    CHARGE = "CHARGE"
    PRECEPTOR = "PRECEPTOR"
    NEW = "NEW"
    GENERAL = "GENERAL"
    NIGHT_ONLY = "NIGHT_ONLY"  # 나이트 전담: N과 OFF만, 월 나이트 15일 이내 (복지부 야간근무 가이드라인)


class NurseRank(StrEnum):
    """조직상 직급. 프리셉터와 경력은 직급과 독립된 속성이다."""
    HEAD_NURSE = "HEAD_NURSE"
    CHARGE_NURSE = "CHARGE_NURSE"
    STAFF_NURSE = "STAFF_NURSE"


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


@dataclass(frozen=True)
class RuleSetting:
    enabled: bool
    severity: Severity
    parameters: dict = field(default_factory=dict)


NIGHT_ONLY_MAX_NIGHTS = 15  # 나이트 전담 월 나이트 상한
SENIOR_ROLES = frozenset({DutyRole.CHARGE, DutyRole.PRECEPTOR})  # 근무마다 1명 이상 있어야 하는 고연차


@dataclass(frozen=True)
class Rules:
    """규칙 코드별 설정. 생성기와 검증기가 같은 파라미터와 심각도를 사용한다."""
    settings: dict[str, RuleSetting]

    def setting(self, code: str) -> RuleSetting:
        """나중에 카탈로그에 추가된 규칙은 저장된 설정이 없을 수 있다 → 카탈로그 기본값"""
        if code in self.settings:
            return self.settings[code]
        from app.domain.rule_catalog import CATALOG
        d = CATALOG[code]
        return RuleSetting(d.enabled, Severity(d.severity), dict(d.params))

    def enabled(self, code: str) -> bool:
        return self.setting(code).enabled

    def enforced(self, code: str) -> bool:
        return self.enabled(code) and self.setting(code).severity == Severity.HARD

    def parameter(self, code: str, key: str):
        return self.setting(code).parameters[key]

    def severity_for(self, code: str) -> Severity:
        return self.setting(code).severity

    @staticmethod
    def preset(p: Preset, d: int, e: int, n: int) -> "Rules":
        from copy import deepcopy
        from app.domain.rule_catalog import CATALOG, MINIMAL_OFF
        return Rules({code: RuleSetting(definition.enabled and not (p == Preset.MINIMAL and code in MINIMAL_OFF),
                                        Severity(definition.severity),
                                        {"D": d, "E": e, "N": n} if code == "COVERAGE" else deepcopy(definition.params))
                      for code, definition in CATALOG.items()})

    def required(self, duty: Duty) -> int:
        return self.settings["COVERAGE"].parameters.get(duty, 0)


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
    rank: NurseRank = NurseRank.STAFF_NURSE
    hire_year: int | None = None
    is_preceptor: bool = False

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
