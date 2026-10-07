"""API-REQ-01~07 연차·희망 오프·희망 근무 신청"""
from conftest import NEXT, YM, add_nurses, err, fill_rotation, member, ward_head

D = [NEXT.replace(day=i).isoformat() for i in range(1, 29)]


def _req(n, type_="ANNUAL_LEAVE", dates=(D[19],), **kw):
    return n("POST", "/wards/me/requests", {"type": type_, "targetDates": list(dates), "reasonCode": "PERSONAL", **kw})


def test_create_validation():
    h = ward_head()
    n, _ = member(h)
    err(_req(n, reasonCode="ETC"), 400, "VALIDATION_ERROR")
    err(_req(n, "PREFERRED_SHIFT"), 400, "VALIDATION_ERROR")
    err(_req(n, dates=[D[0], D[0]]), 400, "VALIDATION_ERROR")
    err(_req(n, dates=["2020-01-01"]), 422, "PAST_DATE")
    r = _req(n, "PREFERRED_SHIFT", preferredDuty="E")
    assert r.status_code == 201 and r.json()["data"]["preferredDuty"] == "E"
    err(_req(n, "PREFERRED_OFF", dates=[D[19]]), 409, "DUPLICATE_REQUEST")
    err(_req(n, "PREFERRED_OFF", dates=D[:5]), 422, "REQUEST_LIMIT_EXCEEDED")
    assert _req(n, "PREFERRED_OFF", dates=D[:4]).status_code == 201


def test_visibility():
    h = ward_head()
    n, _ = member(h)
    o, _ = member(h)
    rid = _req(n).json()["data"]["id"]
    assert n("GET", f"/wards/me/requests/me?yearMonth={YM}").json()["meta"]["totalElements"] == 1
    assert n.data("GET", f"/wards/me/requests/{rid}")["id"] == rid
    err(o("GET", f"/wards/me/requests/{rid}"), 404, "REQUEST_NOT_FOUND")
    err(o("POST", f"/wards/me/requests/{rid}/cancel"), 404, "REQUEST_NOT_FOUND")
    err(n("GET", "/wards/me/requests"), 403, "FORBIDDEN")
    assert h("GET", "/wards/me/requests?status=PENDING").json()["meta"]["totalElements"] == 1


def test_annual_leave_flows_into_draft_and_blocks_confirm():
    h = ward_head()
    add_nurses(h, 3)
    n, mine = member(h)
    s = h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    rid = _req(n, dates=[D[19], D[20]]).json()["data"]["id"]
    s = fill_rotation(h, s)
    err(h("POST", f"/wards/me/schedules/{s['id']}/confirm", {}), 409, "PENDING_REQUESTS_EXIST")
    err(h("POST", f"/wards/me/requests/{rid}/reject", {}), 400, "REASON_REQUIRED")
    a = h.data("POST", f"/wards/me/requests/{rid}/approve")
    assert a["request"]["status"] == "APPROVED" and len(a["scheduleImpact"]["changedCells"]) == 2
    err(h("POST", f"/wards/me/requests/{rid}/approve"), 409, "REQUEST_ALREADY_PROCESSED")
    cells = {c["date"]: c for c in h.data("GET", f"/wards/me/schedules/{s['id']}")["cells"] if c["nurseId"] == mine}
    assert cells[D[19]]["dutyCode"] == "AL" and not cells[D[19]]["editable"]
    # 본인 취소 → 초안에서 AL 제거
    assert n.data("POST", f"/wards/me/requests/{rid}/cancel")["status"] == "CANCELLED"
    cells = {c["date"]: c for c in h.data("GET", f"/wards/me/schedules/{s['id']}")["cells"] if c["nurseId"] == mine}
    assert cells[D[19]]["dutyCode"] is None
    err(n("POST", f"/wards/me/requests/{rid}/cancel"), 409, "REQUEST_NOT_CANCELLABLE")


def test_confirmed_month_rejects_changes():
    h = ward_head()
    add_nurses(h, 3)
    n, _ = member(h)
    rid = _req(n, dates=[D[5]]).json()["data"]["id"]
    h.data("POST", f"/wards/me/requests/{rid}/approve")
    s = h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    assert "AL" in {c["dutyCode"] for c in s["cells"]}, "생성 시 승인된 연차를 미리 채운다"
    cov = next(r for r in h.data("GET", "/wards/me/rules") if r["code"] == "COVERAGE")
    h.data("PATCH", f"/wards/me/rules/{cov['id']}", {"severity": "SOFT"})
    soft = [v["id"] for v in h.data("GET", f"/wards/me/schedules/{s['id']}/violations?size=100")]
    h.data("POST", f"/wards/me/schedules/{s['id']}/confirm", {"acknowledgedSoftViolationIds": soft})
    err(_req(n, dates=[D[6]]), 409, "SCHEDULE_CONFIRMED")
    err(n("POST", f"/wards/me/requests/{rid}/cancel"), 409, "SCHEDULE_CONFIRMED")
