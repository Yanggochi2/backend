"""규칙의 기본값·파라미터 계약. HTTP와 저장소에 의존하지 않는다."""
from copy import deepcopy
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RuleDef:
    name: str
    severity: str
    locked: bool = False
    params: dict = field(default_factory=dict)


CATALOG = {
    "COVERAGE": RuleDef("듀티별 정확한 인원", "HARD", params={"D": 0, "E": 0, "N": 0}),
    "MAX_CONSECUTIVE_NIGHTS": RuleDef("연속 야간 최대 일수", "HARD", params={"max": 3}),
    "MAX_CONSECUTIVE_WORK": RuleDef("연속 근무 최대 일수", "HARD", params={"max": 5}),
    "NIGHT_TO_DAY": RuleDef("N 다음날 D 금지", "HARD"),
    "CHARGE_DAY_ONLY": RuleDef("차지 허용 근무", "HARD", params={"allowedDuties": ["D"]}),
    "PREGNANT_NIGHT": RuleDef("임신 중 N 금지", "HARD", locked=True),
    "NEW_NIGHT_ALONE": RuleDef("신입 N 지원 인원", "HARD", params={"minExperienced": 1}),
    "OUT_OF_AFFILIATION": RuleDef("소속 기간 밖 배정 금지", "HARD", locked=True),
    "OFF_TARGET": RuleDef("월 OFF 목표", "SOFT", params={"maxDeviation": 1}),
    "WISH_OFF_IGNORED": RuleDef("희망 오프 반영", "SOFT"),
    "WISH_DUTY_IGNORED": RuleDef("희망 근무 반영", "SOFT"),
    "PRECEPTOR_MISMATCH": RuleDef("프리셉터·신입 듀티 일치", "SOFT"),
}
MINIMAL_OFF = {"MAX_CONSECUTIVE_NIGHTS", "MAX_CONSECUTIVE_WORK", "NIGHT_TO_DAY"}


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
