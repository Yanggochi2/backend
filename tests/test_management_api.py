from datetime import datetime, timedelta
from unittest.mock import patch

from conftest import Api, NEXT, YM, err, member, ward_head

PATH = f"/wards/me/off-request-deadlines/{YM}"


def request(n, day=1, kind="PREFERRED_OFF"):
    return n("POST", "/wards/me/requests", {"type": kind, "targetDates": [NEXT.replace(day=day).isoformat()],
                                           "reasonCode": "PERSONAL"})


def test_deadline_close_reopen_and_other_request_types():
    h = ward_head()
    n, _ = member(h)
    assert n.data("GET", PATH) == {"yearMonth": YM, "closesAt": None, "manuallyClosed": False,
                                   "isClosed": False, "version": 0}
    assert request(n).status_code == 201
    closed = h.data("PUT", PATH, {"closesAt": None, "manuallyClosed": True, "version": 0})
    assert closed["isClosed"] and closed["version"] == 1
    err(request(n, 2), 409, "OFF_REQUEST_CLOSED")
    # 마감은 기존 신청·연차 승인/취소나 다른 종류를 차단하지 않는다.
    assert request(n, 3, "ANNUAL_LEAVE").status_code == 201
    rid = n.data("GET", f"/wards/me/requests/me?yearMonth={YM}")[0]["id"]
    assert n.data("POST", f"/wards/me/requests/{rid}/cancel")["status"] == "CANCELLED"
    err(h("PUT", PATH, {"closesAt": None, "version": 0}), 409, "VERSION_CONFLICT")
    opened = h.data("PUT", PATH, {"closesAt": None, "version": 1})
    assert not opened["isClosed"] and request(n, 2).status_code == 201


def test_deadline_timezone_and_exact_boundary():
    h = ward_head()
    n, _ = member(h)
    instant = datetime(2026, 10, 25, 9, 0)  # DB UTC
    h.data("PUT", PATH, {"closesAt": "2026-10-25T18:00:00+09:00", "version": 0})
    with patch("app.business.off_deadlines.now", return_value=instant - timedelta(microseconds=1)):
        assert not n.data("GET", PATH)["isClosed"]
    with patch("app.business.off_deadlines.now", return_value=instant):
        result = n.data("GET", PATH)
        assert result["isClosed"] and result["closesAt"] == "2026-10-25T09:00:00Z"
        err(request(n), 409, "OFF_REQUEST_CLOSED")
    err(h("PUT", PATH, {"closesAt": "2026-10-25T18:00:00", "version": 1}), 400, "VALIDATION_ERROR")
    err(h("PUT", PATH, {"closesAt": None, "manuallyClosed": "false", "version": 1}), 400, "VALIDATION_ERROR")
    err(h("PUT", PATH, {"closesAt": None, "version": True}), 400, "VALIDATION_ERROR")
    err(h("GET", "/wards/me/off-request-deadlines/2026-13"), 400, "INVALID_YEAR_MONTH")


def test_deadline_permissions_isolation_and_csrf():
    h = ward_head()
    n, _ = member(h)
    other = ward_head()
    err(Api()("GET", PATH), 401, "UNAUTHENTICATED")
    err(n("PUT", PATH, {"closesAt": None, "version": 0}), 403, "FORBIDDEN")
    err(h("PUT", PATH, {"closesAt": None, "version": 0}, headers={"X-CSRF-Token": "wrong"}),
        403, "CSRF_TOKEN_INVALID")
    h.data("PUT", PATH, {"closesAt": None, "manuallyClosed": True, "version": 0})
    assert other.data("GET", PATH)["version"] == 0
    assert request(other).status_code == 201


def test_charge_role_changes_revalidate_and_do_not_grant_permission():
    h = ward_head()
    n, nid = member(h)
    s = h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    h.data("PATCH", f"/wards/me/schedules/{s['id']}/cells", {"baseVersion": s["version"], "changes": [
        {"nurseId": nid, "date": NEXT.isoformat(), "dutyCode": "E"}]})
    current = h.data("GET", f"/wards/me/nurses/{nid}")
    path = f"/wards/me/nurses/{nid}/charge"
    changed = h.data("PUT", path, {"isCharge": True, "version": current["version"]})
    assert changed["nurse"]["dutyRole"] == "CHARGE" and changed["nurse"]["role"] == "NURSE"
    assert changed["violations"]
    assert [x["id"] for x in h.data("GET", "/wards/me/charges")] == [nid]
    err(h("PUT", path, {"isCharge": False, "version": current["version"]}), 409, "VERSION_CONFLICT")
    err(n("PUT", path, {"isCharge": False, "version": changed["nurse"]["version"]}), 403, "FORBIDDEN")
    other = ward_head()
    err(other("PUT", path, {"isCharge": True, "version": 1}), 404, "NURSE_NOT_FOUND")
    h.data("PUT", path, {"isCharge": False, "version": changed["nurse"]["version"]})
    assert h.data("GET", "/wards/me/charges") == []
    err(n("GET", "/wards/me/charges"), 403, "FORBIDDEN")


def test_catalog_rule_parameters_reach_generation_and_validation():
    h = ward_head(0, 1, 0, preset="MINIMAL")
    catalog = {r["code"]: r for r in h.data("GET", "/wards/me/rule-catalog")}
    assert catalog["CHARGE_DAY_ONLY"]["defaultParameters"] == {"allowedDuties": ["D"]}
    head = h("GET", "/wards/me/nurses").json()["data"][0]
    h.data("PUT", f"/wards/me/nurses/{head['id']}/charge", {"isCharge": True, "version": head["version"]})
    s = h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    generation = f"/wards/me/schedules/{s['id']}/generations"
    assert h.data("POST", generation, {})["status"] == "NO_SOLUTION"
    rule = next(r for r in h.data("GET", "/wards/me/rules") if r["code"] == "CHARGE_DAY_ONLY")
    h.data("PATCH", f"/wards/me/rules/{rule['id']}", {"parameters": {"allowedDuties": ["E"]}})
    assert h.data("POST", generation, {})["status"] == "SUCCEEDED"
    assert all(c["dutyCode"] == "E" for c in h.data("GET", f"/wards/me/schedules/{s['id']}")["cells"])


def test_holiday_override_reset_changes_off_target_and_is_scoped():
    h = ward_head()
    other = ward_head()
    n, _ = member(h)
    h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    initial = h.data("GET", f"/wards/me/off-targets/{YM}")["targetCount"]
    day = NEXT.replace(day=20).isoformat()  # 기본 공휴일과 겹치지 않는다
    h.data("PUT", f"/wards/me/holidays/{day}", {"isHoliday": True, "name": "병원 휴일"})
    assert h.data("GET", f"/wards/me/off-targets/{YM}")["targetCount"] == initial + 1
    err(n("DELETE", f"/wards/me/holidays/{day}"), 403, "FORBIDDEN")
    err(other("DELETE", f"/wards/me/holidays/{day}"), 404, "HOLIDAY_OVERRIDE_NOT_FOUND")
    assert h("DELETE", f"/wards/me/holidays/{day}").status_code == 204
    assert h.data("GET", f"/wards/me/off-targets/{YM}")["targetCount"] == initial
