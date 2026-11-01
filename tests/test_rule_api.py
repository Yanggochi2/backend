"""API-RULE-01~07 근무 규칙·공휴일·OFF 목표"""
from conftest import NEXT, YM, add_nurses, err, fill_rotation, member, ward_head


def test_rules_patch_and_preset():
    h = ward_head(2, 2, 1)
    n, _ = member(h)
    err(n("GET", "/wards/me/rules"), 401, "MANAGEMENT_LINK_REQUIRED")
    by = {r["code"]: r for r in h.data("GET", "/wards/me/rules")}
    assert by["COVERAGE"]["parameters"] == {"D": 2, "E": 2, "N": 1} and by["PREGNANT_NIGHT"]["locked"]
    err(h("PATCH", f"/wards/me/rules/{by['PREGNANT_NIGHT']['id']}", {"enabled": False}), 409, "RULE_CONFLICT")
    err(h("PATCH", f"/wards/me/rules/{by['MAX_CONSECUTIVE_NIGHTS']['id']}", {"parameters": {"max": 0}}),
        400, "INVALID_RULE_VALUE")
    err(h("PATCH", f"/wards/me/rules/{by['MAX_CONSECUTIVE_NIGHTS']['id']}", {"parameters": {"max": True}}),
        400, "INVALID_RULE_VALUE")
    # enabled: null은 생략과 같다 (DB NOT NULL 컬럼에 None이 들어가 500이 나지 않게)
    assert h.data("PATCH", f"/wards/me/rules/{by['NIGHT_TO_DAY']['id']}", {"enabled": None})["rule"]["enabled"]
    rid = by["MAX_CONSECUTIVE_NIGHTS"]["id"]
    r = h.data("PATCH", f"/wards/me/rules/{rid}", {"parameters": {"max": 4}, "reason": "인력", "version": 1})
    assert r["rule"]["parameters"] == {"max": 4} and r["violationSummary"] == []
    err(h("PATCH", f"/wards/me/rules/{rid}", {"enabled": False, "version": 1}), 409, "VERSION_CONFLICT")
    err(h("POST", "/wards/me/rule-presets/NOPE/apply"), 404, "PRESET_NOT_FOUND")
    rs = {r["code"]: r for r in h.data("POST", "/wards/me/rule-presets/MINIMAL/apply")}
    assert not rs["NIGHT_TO_DAY"]["enabled"] and rs["COVERAGE"]["parameters"] == {"D": 2, "E": 2, "N": 1}
    # 필요 인원 변경은 COVERAGE 파라미터 → 병동 정보에도 반영
    h.data("PATCH", f"/wards/me/rules/{rs['COVERAGE']['id']}", {"parameters": {"D": 3, "E": 2, "N": 2}})
    assert h.data("GET", "/wards/me")["requiredStaff"] == {"D": 3, "E": 2, "N": 2}


def test_softened_rule_no_longer_blocks_confirm():
    h = ward_head()
    add_nurses(h, 4)
    s = h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    off = next(r for r in h.data("GET", "/wards/me/rules") if r["code"] == "OFF_TARGET")
    h.data("PATCH", f"/wards/me/rules/{off['id']}", {"severity": "SOFT"})
    cov = next(r for r in h.data("GET", "/wards/me/rules") if r["code"] == "COVERAGE")
    r = h.data("PATCH", f"/wards/me/rules/{cov['id']}", {"severity": "SOFT"})
    assert r["violationSummary"][0]["hardCount"] == 0 and r["violationSummary"][0]["softCount"] > 0
    assert h.data("GET", f"/wards/me/schedules/{s['id']}")["readiness"]["confirmable"]


def test_holidays():
    h = ward_head()
    err(h("GET", "/wards/me/holidays?yearMonth=2026-13"), 400, "INVALID_YEAR_MONTH")
    oct_ = {x["date"]: x for x in h.data("GET", "/wards/me/holidays?yearMonth=2026-10")}
    assert oct_["2026-10-03"]["source"] == "DEFAULT" and oct_["2026-10-09"]["isHoliday"]
    day = NEXT.replace(day=10).isoformat()
    r = h.data("PUT", f"/wards/me/holidays/{day}", {"isHoliday": True, "name": "병원 창립일"})
    assert r == {"date": day, "name": "병원 창립일", "isHoliday": True, "source": "WARD", "reason": None}
    off = h.data("PUT", "/wards/me/holidays/2026-10-09", {"isHoliday": False, "reason": "근무일 지정"})
    assert off["isHoliday"] is False
