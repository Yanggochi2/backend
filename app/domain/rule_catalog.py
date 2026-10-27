"""규칙의 기본값·파라미터 계약. HTTP와 저장소에 의존하지 않는다."""
from copy import deepcopy
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RuleDef:
    name: str
    severity: str
    locked: bool = False
    params: dict = field(default_factory=dict)
    enabled: bool = True  # 새 병동의 기본 켜짐


CATALOG = {
    "COVERAGE": RuleDef("듀티별 정확한 인원", "HARD", params={"D": 0, "E": 0, "N": 0}),
    "MAX_CONSECUTIVE_NIGHTS": RuleDef("연속 야간 최대 일수", "HARD", params={"max": 3}),
    "MAX_CONSECUTIVE_WORK": RuleDef("연속 근무 최대 일수", "HARD", params={"max": 5}),
    "NIGHT_TO_DAY": RuleDef("역순환 금지 (E→D, N→D, N→E)", "HARD"),
    "NIGHT_REST": RuleDef("나이트 후 OFF 1일", "HARD"),
    "NIGHT_REST_2D": RuleDef("나이트 후 OFF 2일 권장", "SOFT"),
    # 차지 직급의 듀티 제한과 근무별 배치 여부는 클라이언트 확인 전까지 적용하지 않는다.
    "CHARGE_DAY_ONLY": RuleDef("차지 허용 근무", "HARD", params={"allowedDuties": ["D", "E", "N"]}, enabled=False),
    "CHARGE_EVERY_SHIFT": RuleDef("근무마다 차지 1명 이상", "HARD", enabled=False),
    "SENIOR_EVERY_SHIFT": RuleDef("근무마다 고연차 1명 이상", "HARD", enabled=False),
    "NIGHT_ONLY_SHIFT": RuleDef("나이트 전담은 N만", "HARD", locked=True, enabled=False),
    "PREGNANT_NIGHT": RuleDef("임신 중 N 금지", "HARD", locked=True),
    "NEW_NIGHT_ALONE": RuleDef("신입 N 지원 인원", "HARD", params={"minExperienced": 1}),
    "OUT_OF_AFFILIATION": RuleDef("소속 기간 밖 배정 금지", "HARD", locked=True),
    "OFF_TARGET": RuleDef("월 OFF 목표", "SOFT", params={"maxDeviation": 1}),
    "WISH_OFF_IGNORED": RuleDef("희망 오프 반영", "SOFT", enabled=False),
    "WISH_DUTY_IGNORED": RuleDef("희망 근무 반영", "SOFT", enabled=False),
    "PRECEPTOR_MISMATCH": RuleDef("프리셉터·신입 듀티 일치", "SOFT", enabled=False),
    # 근거: docs/superpowers/specs/2026-10-26-cp-sat-generator-design.md (복지부 야간근무 가이드라인, 병원 단협)
    "MAX_MONTHLY_NIGHTS": RuleDef("월 야간 최대 횟수", "SOFT", params={"max": 6}),
    "NIGHT_OFF_DAY": RuleDef("나이트-오프-데이 피하기", "SOFT"),
    "ISOLATED_SHIFT": RuleDef("고립된 근무·휴무 피하기", "SOFT"),
    "WEEKLY_REST": RuleDef("주마다 연속 휴무 2일", "SOFT"),
    "SHIFT_BLOCK": RuleDef("같은 근무 2일 이상 이어서", "SOFT"),
}
MINIMAL_OFF = {"MAX_CONSECUTIVE_NIGHTS", "MAX_CONSECUTIVE_WORK", "NIGHT_TO_DAY", "NIGHT_REST", "NIGHT_REST_2D"}


def parameters(code: str, supplied: dict) -> dict:
    """구형 빈 파라미터는 기본값으로 읽되, 알 수 없는 키와 잘못된 타입은 거절한다."""
    definition = CATALOG[code]
    if not isinstance(supplied, dict) or set(supplied) - set(definition.params):
        raise ValueError(f"parameters 허용 키: {sorted(definition.params)}")
    values = {**deepcopy(definition.params), **deepcopy(supplied)}
    if code == "COVERAGE":
        if set(supplied) != {"D", "E", "N"} or any(type(v) is not int or v < 0 for v in values.values()) or not sum(values.values()):
            raise ValueError("D/E/N은 각각 0 이상 정수, 합계 1 이상이어야 합니다")
    for key in ("max", "minExperienced"):
        if key in values and (type(values[key]) is not int or not 1 <= values[key] <= 31):
            raise ValueError(f"{key}는 1~31 정수여야 합니다")
    if "maxDeviation" in values and (type(values["maxDeviation"]) is not int or not 0 <= values["maxDeviation"] <= 31):
        raise ValueError("maxDeviation은 0~31 정수여야 합니다")
    if "allowedDuties" in values:
        duties = values["allowedDuties"]
        if (not isinstance(duties, list) or not duties or any(x not in ("D", "E", "N") for x in duties)
                or len(duties) != len(set(duties))):
            raise ValueError("allowedDuties는 중복 없는 D/E/N 목록이어야 합니다")
    return values
