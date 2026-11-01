from datetime import datetime, timedelta
from unittest.mock import patch

from conftest import Api, NEXT, YM, err, member, ward_head

PATH = f"/wards/me/off-request-deadlines/{YM}"


def request(n, day=1, kind="PREFERRED_OFF"):
    return n("POST", "/wards/me/requests", {"type": kind, "targetDates": [NEXT.replace(day=day).isoformat()],
                                           "reasonCode": "PERSONAL"})


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
    err(n("PUT", path, {"isCharge": False, "version": changed["nurse"]["version"]}), 401, "MANAGEMENT_LINK_REQUIRED")
    other = ward_head()
    err(other("PUT", path, {"isCharge": True, "version": 1}), 404, "NURSE_NOT_FOUND")
    h.data("PUT", path, {"isCharge": False, "version": changed["nurse"]["version"]})
    assert h.data("GET", "/wards/me/charges") == []
    err(n("GET", "/wards/me/charges"), 401, "MANAGEMENT_LINK_REQUIRED")


def test_holiday_override_reset_changes_off_target_and_is_scoped():
    h = ward_head()
    other = ward_head()
    n, _ = member(h)
    h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    initial = h.data("GET", f"/wards/me/off-targets/{YM}")["targetCount"]
    day = next((NEXT.replace(day=i)).isoformat() for i in range(10, 20) if NEXT.replace(day=i).weekday() < 5)  # 기본 공휴일과 겹치지 않는다
    h.data("PUT", f"/wards/me/holidays/{day}", {"isHoliday": True, "name": "병원 휴일"})
    assert h.data("GET", f"/wards/me/off-targets/{YM}")["targetCount"] == initial + 1
    err(n("DELETE", f"/wards/me/holidays/{day}"), 401, "MANAGEMENT_LINK_REQUIRED")
    err(other("DELETE", f"/wards/me/holidays/{day}"), 404, "HOLIDAY_OVERRIDE_NOT_FOUND")
    assert h("DELETE", f"/wards/me/holidays/{day}").status_code == 204
    assert h.data("GET", f"/wards/me/off-targets/{YM}")["targetCount"] == initial
