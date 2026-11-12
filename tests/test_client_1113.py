"""11/13 클라이언트 요청: 간호조무사, 강제 OFF, 대시보드."""
from conftest import Api, NURSE, NEXT, YM, err, ward_head


def test_assistant_does_not_satisfy_nurse_coverage_and_force_off_is_audited_action():
    h = ward_head(d=1, e=0, n=0)
    assistant = h.data("POST", "/wards/me/nurses", {
        **NURSE, "name": "김조무", "staffType": "NURSING_ASSISTANT",
    })
    schedule = h.data("POST", "/wards/me/schedules", {"yearMonth": YM}, status=201)
    path = f"/wards/me/schedules/{schedule['id']}"
    assigned = h.data("PATCH", path + "/cells", {
        "baseVersion": schedule["version"],
        "changes": [{"nurseId": assistant["id"], "date": NEXT.isoformat(), "dutyCode": "D"}],
    })
    coverage = next(row for row in assigned["coverage"]
                    if row["date"] == NEXT.isoformat() and row["dutyCode"] == "D")
    assert coverage["actualCount"] == 0 and coverage["status"] == "UNDER"

    forced = h.data("PATCH", path + "/forced-offs", {
        "baseVersion": assigned["version"], "reason": "인력 운영 조정",
        "cells": [{"nurseId": assistant["id"], "date": NEXT.isoformat()}],
    })
    assert forced["changedCells"] == 1
    assert next(cell for cell in h.data("GET", path)["cells"]
                if cell["nurseId"] == assistant["id"] and cell["date"] == NEXT.isoformat())["dutyCode"] == "O"


def test_dashboard_returns_graph_ready_monthly_aggregates():
    h = ward_head(d=1, e=0, n=0)
    assistant = h.data("POST", "/wards/me/nurses", {
        **NURSE, "name": "박조무", "staffType": "NURSING_ASSISTANT",
    })
    schedule = h.data("POST", "/wards/me/schedules", {"yearMonth": YM}, status=201)
    h.data("PATCH", f"/wards/me/schedules/{schedule['id']}/forced-offs", {
        "baseVersion": schedule["version"],
        "cells": [{"nurseId": assistant["id"], "date": NEXT.isoformat()}],
    })

    dashboard = h.data("GET", f"/wards/me/dashboard?yearMonth={YM}")
    composition = {row["staffType"]: row["count"] for row in dashboard["staffComposition"]}
    assert composition == {"NURSE": 1, "NURSING_ASSISTANT": 1}
    assert dashboard["schedule"]["id"] == schedule["id"]
    assert {row["dutyCode"] for row in dashboard["dutyDistribution"]} == {"D", "E", "N", "O", "AL", "ED"}
    assert dashboard["offSummary"]["assigned"] == 1
    assert len(dashboard["dailyCoverage"]) > 0
    assert {row["name"] for row in dashboard["nurseWorkloads"]} == {"수간호사", "박조무"}
    err(Api()("GET", f"/wards/me/dashboard?yearMonth={YM}"), 401, "UNAUTHENTICATED")
