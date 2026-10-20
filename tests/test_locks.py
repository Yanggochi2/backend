"""API-SCH-13~15 편집 잠금"""
from conftest import NEXT, YM, add_nurses, err, member, ward_head


def _setup():
    h = ward_head()
    add_nurses(h, 2)
    h2, nid = member(h, "부수간")
    h.data("POST", f"/wards/me/head-nurses/{nid}/grant")
    return h, h2, h.data("POST", "/wards/me/schedules", {"yearMonth": YM})


def test_lock_requires_token_and_does_not_bump_version():
    h, h2, s = _setup()
    url = f"/wards/me/schedules/{s['id']}"
    lock = h.data("POST", url + "/lock", status=201)
    assert lock["lock"]["holderId"] and lock["lockToken"]
    assert h.data("GET", url)["version"] == s["version"], "잠금은 내용 버전을 올리지 않는다"
    err(h2("POST", url + "/lock"), 409, "LOCK_ALREADY_HELD")
    change = {"baseVersion": s["version"],
              "changes": [{"nurseId": s["nurses"][0]["id"], "date": NEXT.isoformat(), "dutyCode": "D"}]}
    err(h("PATCH", url + "/cells", change), 423, "SCHEDULE_LOCKED")
    err(h2("PATCH", url + "/cells", change, headers={"X-Schedule-Lock-Token": lock["lockToken"]}),
        423, "SCHEDULE_LOCKED")
    h.data("PATCH", url + "/cells", change, headers={"X-Schedule-Lock-Token": lock["lockToken"]})
    err(h2("POST", url + "/confirm", {}), 423, "SCHEDULE_LOCKED")
    err(h("DELETE", url + "/lock", headers={"X-Schedule-Lock-Token": "x"}), 403, "LOCK_TOKEN_INVALID")
    assert h("DELETE", url + "/lock", headers={"X-Schedule-Lock-Token": lock["lockToken"]}).status_code == 204
    assert h.data("GET", url)["lock"] is None


def test_takeover_needs_reason():
    h, h2, s = _setup()
    url = f"/wards/me/schedules/{s['id']}"
    old = h.data("POST", url + "/lock")["lockToken"]
    err(h2("POST", url + "/lock/takeover", {}), 400, "REASON_REQUIRED")
    new = h2.data("POST", url + "/lock/takeover", {"reason": "급한 수정"})
    assert new["lockToken"] != old
    err(h("DELETE", url + "/lock", headers={"X-Schedule-Lock-Token": old}), 403, "LOCK_TOKEN_INVALID")
