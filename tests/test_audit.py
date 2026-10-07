"""API-SEC-01 감사 로그"""
from conftest import YM, add_nurses, err, member, ward_head


def test_audit_logs():
    h = ward_head()
    n, _ = member(h)
    [a] = add_nurses(h, 1)
    h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    logs = h("GET", "/wards/me/audit-logs?size=100").json()
    actions = [x["actionType"] for x in logs["data"]]
    assert {"WARD_CREATED", "MEMBERSHIP_APPROVED", "NURSE_REGISTERED", "SCHEDULE_CREATED"} <= set(actions)
    reg = h.data("GET", "/wards/me/audit-logs?actionType=NURSE_REGISTERED")[0]
    assert reg["targetType"] == "NURSE" and reg["targetId"] == a and reg["actorId"]
    err(h("GET", "/wards/me/audit-logs?from=2026-12-01&to=2026-01-01"), 400, "INVALID_DATE_RANGE")
    assert h.data("GET", "/wards/me/audit-logs?from=2000-01-01&to=2000-01-02") == []
    err(n("GET", "/wards/me/audit-logs"), 403, "FORBIDDEN")
    # 다른 병동 로그는 섞이지 않는다
    assert all(x["actionType"] != "NURSE_REGISTERED" for x in ward_head().data("GET", "/wards/me/audit-logs"))
