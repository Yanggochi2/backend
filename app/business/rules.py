"""RULE-01·02·04. 규칙은 병동별 행(WardRule)으로 저장하고 검증·생성용 Rules로 변환한다"""
import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business.common import ApiException, month_end
from app.domain.model import Preset, Rules, Severity
from app.persistence.models import Holiday, WardRule

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


def check_staffing(staff: dict) -> None:
    if set(staff) != {"D", "E", "N"} or any(not isinstance(v, int) or isinstance(v, bool) or v < 0
                                            for v in staff.values()) or sum(staff.values()) == 0:
        raise ApiException(422, "INVALID_STAFFING", "필요 인원은 D/E/N 각각 0 이상, 합계 1 이상이어야 합니다")


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
