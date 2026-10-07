"""RULE-01·02·04. 규칙은 병동별 행(WardRule)으로 저장하고 검증·생성용 Rules로 변환한다"""
import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business import audit, members, schedules
from app.business.common import ApiException, bad_request, conflict, month_end, not_found
from app.domain.model import Preset, Rules, ScheduleStatus, Severity
from app.persistence.models import Holiday, Schedule, WardRule

HARD, SOFT = Severity.HARD, Severity.SOFT


@dataclass(frozen=True)
class RuleDef:
    name: str
    severity: Severity
    locked: bool = False  # 법적 보호 규칙: 끄거나 SOFT로 낮출 수 없다
    params: dict = field(default_factory=dict)


CATALOG = {
    "COVERAGE": RuleDef("듀티별 필요 인원", HARD, params={"D": 0, "E": 0, "N": 0}),
    "MAX_CONSECUTIVE_NIGHTS": RuleDef("연속 야간 최대 일수", HARD, params={"max": 3}),
    "MAX_CONSECUTIVE_WORK": RuleDef("연속 근무 최대 일수", HARD, params={"max": 5}),
    "NIGHT_TO_DAY": RuleDef("N 다음날 D 금지", HARD),
    "CHARGE_DAY_ONLY": RuleDef("차지 간호사는 D만", HARD),
    "PREGNANT_NIGHT": RuleDef("임신 중 N 금지", HARD, locked=True),
    "NEW_NIGHT_ALONE": RuleDef("신입 N 단독 배치 금지", HARD),
    "OUT_OF_AFFILIATION": RuleDef("소속 기간 밖 배정 금지", HARD, locked=True),
    "OFF_TARGET": RuleDef("월 OFF 목표", SOFT),
    "WISH_OFF_IGNORED": RuleDef("희망 오프 반영", SOFT),
    "WISH_DUTY_IGNORED": RuleDef("희망 근무 반영", SOFT),
    "PRECEPTOR_MISMATCH": RuleDef("프리셉터·신입 듀티 일치", SOFT),
}
# MINIMAL 프리셋에서 끄는 규칙
_MINIMAL_OFF = {"MAX_CONSECUTIVE_NIGHTS", "MAX_CONSECUTIVE_WORK", "NIGHT_TO_DAY"}
# 매년 같은 날짜인 공휴일. 설·추석·대체공휴일 등은 병동에서 보정(RULE-02)한다
# ponytail: 음력·대체공휴일 자동 계산 없음. 필요하면 공공데이터 특일 API 연동
DEFAULT_HOLIDAYS = {(1, 1): "신정", (3, 1): "삼일절", (5, 5): "어린이날", (6, 6): "현충일", (8, 15): "광복절",
                    (10, 3): "개천절", (10, 9): "한글날", (12, 25): "기독탄신일"}


def seed(db: Session, ward_id: uuid.UUID, preset: Preset, staff: dict) -> None:
    for code in CATALOG:
        db.add(WardRule(ward_id=ward_id, code=code, **_preset_values(preset, code, staff)))


def rows(db: Session, ward_id: uuid.UUID) -> dict[str, WardRule]:
    return {r.code: r for r in db.scalars(select(WardRule).where(WardRule.ward_id == ward_id))}


def load(db: Session, ward_id: uuid.UUID) -> Rules:
    rs = rows(db, ward_id)
    staff = rs["COVERAGE"].parameters
    return Rules(staff["D"], staff["E"], staff["N"], rs["MAX_CONSECUTIVE_NIGHTS"].parameters["max"],
                 rs["MAX_CONSECUTIVE_WORK"].parameters["max"], rs["NIGHT_TO_DAY"].enabled,
                 {code: r.severity if r.enabled else None for code, r in rs.items()})


def staffing(db: Session, ward_id: uuid.UUID) -> dict:
    return dict(rows(db, ward_id)["COVERAGE"].parameters)


def list_(db: Session, user_id: uuid.UUID) -> list[dict]:
    m = members.require_head(db, user_id)
    return [view(r) for r in sorted(rows(db, m.ward_id).values(), key=lambda r: list(CATALOG).index(r.code))]


def patch(db: Session, user_id: uuid.UUID, rule_id: uuid.UUID, changes: dict, reason: str | None,
          version: int | None) -> dict:
    m = members.require_head(db, user_id)
    r = db.get(WardRule, rule_id)
    if r is None or r.ward_id != m.ward_id:
        raise not_found("RULE_NOT_FOUND")
    if version is not None and version != r.version:
        raise conflict("VERSION_CONFLICT", "다른 사용자가 먼저 수정했습니다")
    d = CATALOG[r.code]
    enabled = changes.get("enabled", r.enabled)
    severity = changes.get("severity") or r.severity
    params = changes.get("parameters", r.parameters)
    if d.locked and (not enabled or severity != HARD):
        raise conflict("RULE_CONFLICT", f"'{d.name}' 규칙은 끄거나 완화할 수 없습니다")
    _check_params(r.code, params)
    before = view(r)
    r.enabled, r.severity, r.parameters = enabled, severity, dict(params)
    db.flush()
    audit.log(db, m.ward_id, user_id, "RULE_CHANGED", "RULE", r.code, before, {**view(r), "reason": reason})
    return {"rule": view(r), "violationSummary": schedules.violation_summary(db, m.ward_id)}


def apply_preset(db: Session, user_id: uuid.UUID, preset_id: str) -> list[dict]:
    """RULE-04. 필요 인원(COVERAGE 값)은 유지한다"""
    m = members.require_head(db, user_id)
    try:
        preset = Preset(preset_id)
    except ValueError:
        raise not_found("PRESET_NOT_FOUND") from None
    rs = rows(db, m.ward_id)
    staff = rs["COVERAGE"].parameters
    for code, r in rs.items():
        for k, v in _preset_values(preset, code, staff).items():
            setattr(r, k, v)
    db.flush()
    audit.log(db, m.ward_id, user_id, "RULE_PRESET_APPLIED", "WARD", m.ward_id, None, preset)
    return list_(db, user_id)


def view(r: WardRule) -> dict:
    return {"id": r.id, "code": r.code, "name": CATALOG[r.code].name, "severity": r.severity, "enabled": r.enabled,
            "parameters": r.parameters, "locked": CATALOG[r.code].locked, "version": r.version}


def check_staffing(staff: dict) -> None:
    if set(staff) != {"D", "E", "N"} or any(not isinstance(v, int) or isinstance(v, bool) or v < 0
                                            for v in staff.values()) or sum(staff.values()) == 0:
        raise ApiException(422, "INVALID_STAFFING", "필요 인원은 D/E/N 각각 0 이상, 합계 1 이상이어야 합니다")


def _check_params(code: str, params) -> None:
    expected = CATALOG[code].params
    if not isinstance(params, dict) or set(params) != set(expected):
        raise bad_request("INVALID_RULE_VALUE", f"parameters는 {sorted(expected)} 키를 가져야 합니다", "parameters")
    if code == "COVERAGE":
        try:
            check_staffing(params)
        except ApiException:
            raise bad_request("INVALID_RULE_VALUE", "필요 인원은 0 이상 정수, 합계 1 이상", "parameters") from None
    elif "max" in params and (not isinstance(params["max"], int) or not 1 <= params["max"] <= 31):
        raise bad_request("INVALID_RULE_VALUE", "max는 1~31", "parameters")


def _preset_values(preset: Preset, code: str, staff: dict) -> dict:
    d = CATALOG[code]
    return {"enabled": not (preset == Preset.MINIMAL and code in _MINIMAL_OFF), "severity": d.severity,
            "parameters": dict(staff) if code == "COVERAGE" else dict(d.params)}


# --- 공휴일 (RULE-02)
def in_month(db: Session, ward_id: uuid.UUID, first: date) -> list[dict]:
    """기본 공휴일에 병동 보정을 덮어쓴 결과. isHoliday=false는 기본 공휴일을 근무일로 바꾼 보정"""
    out = {first.replace(day=d): {"date": first.replace(day=d).isoformat(), "name": name, "isHoliday": True,
                                  "source": "DEFAULT", "reason": None}
           for (mo, d), name in DEFAULT_HOLIDAYS.items() if mo == first.month}
    for h in db.scalars(select(Holiday).where(Holiday.ward_id == ward_id, Holiday.date >= first,
                                              Holiday.date <= month_end(first))):
        out[h.date] = {"date": h.date.isoformat(), "name": h.name, "isHoliday": h.is_holiday, "source": "WARD",
                       "reason": h.reason}
    return [out[d] for d in sorted(out)]


def holiday_dates(db: Session, ward_id: uuid.UUID, first: date) -> set[date]:
    return {date.fromisoformat(h["date"]) for h in in_month(db, ward_id, first) if h["isHoliday"]}


def holidays(db: Session, user_id: uuid.UUID, first: date) -> list[dict]:
    return in_month(db, members.require_head(db, user_id).ward_id, first)


def put_holiday(db: Session, user_id: uuid.UUID, d: date, is_holiday: bool, name: str | None,
                reason: str | None) -> dict:
    m = members.require_head(db, user_id)
    # 확정된 달의 공휴일이 바뀌면 OFF 목표·통계가 확정본과 어긋난다
    if db.scalar(select(Schedule.id).where(Schedule.ward_id == m.ward_id, Schedule.year_month == d.strftime("%Y-%m"),
                                           Schedule.status.in_([ScheduleStatus.CONFIRMED, ScheduleStatus.ARCHIVED]))):
        raise ApiException(422, "DATE_OUT_OF_SCOPE", "확정된 달의 공휴일은 보정할 수 없습니다")
    h = db.scalar(select(Holiday).where(Holiday.ward_id == m.ward_id, Holiday.date == d))
    before = None if h is None else (h.is_holiday, h.name)
    if h is None:
        h = Holiday(ward_id=m.ward_id, date=d)
        db.add(h)
    h.is_holiday = is_holiday
    h.name = (name or "").strip() or DEFAULT_HOLIDAYS.get((d.month, d.day)) or ("공휴일" if is_holiday else "근무일")
    h.reason = reason
    db.flush()
    audit.log(db, m.ward_id, user_id, "HOLIDAY_CHANGED", "HOLIDAY", d, before, (is_holiday, h.name))
    return next(x for x in in_month(db, m.ward_id, d.replace(day=1)) if x["date"] == d.isoformat())
