"""API-SCH-01~08 근무표 생성·조회·편집·확정"""
from conftest import NURSE, YM, NEXT, add_nurses, err, fill_rotation, member, ward_head


def _ward_with_schedule(extra=4):
    h = ward_head()
    add_nurses(h, extra)
    return h, h.data("POST", "/wards/me/schedules", {"yearMonth": YM}, status=201)


def test_create_and_read():
    h, s = _ward_with_schedule()
    err(h("POST", "/wards/me/schedules", {"yearMonth": "2026/11"}), 400, "INVALID_YEAR_MONTH")
    err(h("POST", "/wards/me/schedules", {"yearMonth": YM}), 409, "SCHEDULE_ALREADY_EXISTS")
    assert s["status"] == "DRAFT" and len(s["nurses"]) == 5
    assert len(s["cells"]) == 5 * len({c["date"] for c in s["cells"]}) and all(c["editable"] for c in s["cells"])
    assert h.data("GET", f"/wards/me/schedules?yearMonth={YM}")["id"] == s["id"]
    cov = h.data("GET", f"/wards/me/schedules/{s['id']}/coverage")
    assert cov[0] == {"date": NEXT.isoformat(), "dutyCode": "D", "actualCount": 0, "requiredCount": 1,
                      "status": "UNDER"}


def test_nurse_cannot_see_draft():
    h, s = _ward_with_schedule()
    n, _ = member(h)
    err(n("GET", f"/wards/me/schedules/{s['id']}"), 404, "SCHEDULE_NOT_FOUND")
    err(n("GET", f"/wards/me/schedules?yearMonth={YM}"), 404, "SCHEDULE_NOT_FOUND")
    err(n("POST", "/wards/me/schedules", {"yearMonth": "2030-01"}), 403, "FORBIDDEN")


def test_edit_cells_is_atomic_and_versioned():
    h, s = _ward_with_schedule()
    a, b = s["nurses"][0]["id"], s["nurses"][1]["id"]
    d1 = NEXT.isoformat()
    url = f"/wards/me/schedules/{s['id']}/cells"
    err(h("PATCH", url, {"baseVersion": s["version"] - 1, "changes": [{"nurseId": a, "date": d1, "dutyCode": "D"}]}),
        409, "VERSION_CONFLICT")
    err(h("PATCH", url, {"baseVersion": s["version"], "changes": [{"nurseId": a, "date": d1, "dutyCode": "AL"}]}),
        422, "PROTECTED_CELL")
    # 하나라도 틀리면 전부 취소 (두 번째 변경이 다른 달)
    bad = [{"nurseId": a, "date": d1, "dutyCode": "D"}, {"nurseId": b, "date": "2020-01-01", "dutyCode": "D"}]
    err(h("PATCH", url, {"baseVersion": s["version"], "changes": bad}), 400, "VALIDATION_ERROR")
    r = h.data("PATCH", url, {"baseVersion": s["version"], "changes": [{"nurseId": a, "date": d1, "dutyCode": "D"},
                                                                       {"nurseId": b, "date": d1, "dutyCode": "N"}]})
    assert r["version"] == s["version"] + 1 and r["changedCells"] == 2
    assert next(c for c in r["coverage"] if c["date"] == d1 and c["dutyCode"] == "D")["status"] == "MET"
    r = h.data("PATCH", url, {"baseVersion": r["version"], "changes": [{"nurseId": a, "date": d1, "dutyCode": None}]})
    assert r["changedCells"] == 1


def test_out_of_affiliation_cell_is_protected():
    h = ward_head()
    late = h.data("POST", "/wards/me/nurses", {**NURSE, "name": "늦게", "affiliationStart": NEXT.replace(day=15)
                                               .isoformat()})["id"]
    s = h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    assert not next(c for c in s["cells"] if c["nurseId"] == late and c["date"] == NEXT.isoformat())["editable"]
    err(h("PATCH", f"/wards/me/schedules/{s['id']}/cells",
          {"baseVersion": s["version"], "changes": [{"nurseId": late, "date": NEXT.isoformat(), "dutyCode": "D"}]}),
        422, "PROTECTED_CELL")


def test_violations_and_confirm():
    h, s = _ward_with_schedule()
    err(h("POST", f"/wards/me/schedules/{s['id']}/confirm", {}), 409, "HARD_VIOLATIONS_EXIST")
    hard = h("GET", f"/wards/me/schedules/{s['id']}/violations?severity=HARD&size=5").json()
    assert hard["meta"]["totalElements"] > 5 and {v["ruleCode"] for v in hard["data"]} == {"COVERAGE"}
    s = fill_rotation(h, s)
    assert s["readiness"]["hardViolations"] == 0 and s["readiness"]["confirmable"]
    soft = [v["id"] for v in h.data("GET", f"/wards/me/schedules/{s['id']}/violations?size=100")]
    c = h.data("POST", f"/wards/me/schedules/{s['id']}/confirm", {"acknowledgedSoftViolationIds": soft})
    assert c["status"] == "CONFIRMED" and c["confirmedAt"] and not any(x["editable"] for x in c["cells"])
    err(h("POST", f"/wards/me/schedules/{s['id']}/confirm", {}), 409, "INVALID_SCHEDULE_STATE")
    err(h("PATCH", f"/wards/me/schedules/{s['id']}/cells", {"baseVersion": c["version"], "changes": [
        {"nurseId": s["nurses"][0]["id"], "date": NEXT.isoformat(), "dutyCode": "O"}]}), 409, "INVALID_SCHEDULE_STATE")


def test_nurse_view_after_confirm_and_cancel():
    h, s = _ward_with_schedule(3)
    n, mine = member(h)
    s = fill_rotation(h, h.data("GET", f"/wards/me/schedules/{s['id']}"))
    h.data("POST", f"/wards/me/schedules/{s['id']}/confirm", {})
    v = n.data("GET", f"/wards/me/schedules?yearMonth={YM}")
    assert [x["nurseId"] for x in v["statistics"]] == [mine], "통계는 본인만"
    assert all(x["nightBlocked"] is None for x in v["nurses"]) and v["readiness"] is None
    url = f"/wards/me/schedules/{s['id']}/confirmation-cancellations"
    err(h("POST", url, {}), 400, "REASON_REQUIRED")
    d = h.data("POST", url, {"reason": "인력 변동"})
    assert d["status"] == "DRAFT" and d["confirmation"]["cancelReason"] == "인력 변동"
    err(h("POST", url, {"reason": "x"}), 409, "INVALID_SCHEDULE_STATE")
    err(n("GET", f"/wards/me/schedules/{s['id']}"), 404, "SCHEDULE_NOT_FOUND")
