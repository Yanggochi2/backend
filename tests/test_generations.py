"""API-GEN-01~05 자동 생성"""
from datetime import timedelta

from conftest import NEXT, YM, add_nurses, confirm_schedule, err, ward_head


def test_generation_succeeds_and_applies():
    h = ward_head(2, 2, 1)
    add_nurses(h, 13)
    s = h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    job = h.data("POST", f"/wards/me/schedules/{s['id']}/generations", {}, status=202)
    assert job["status"] == "SUCCEEDED" and job["hardViolationCount"] == 0 and job["progress"] == 100
    assert h.data("GET", f"/wards/me/generations/{job['id']}")["id"] == job["id"]
    err(h("POST", f"/wards/me/generations/{job['id']}/stop", {}), 409, "JOB_ALREADY_FINISHED")
    after = h.data("GET", f"/wards/me/schedules/{s['id']}")
    assert after["version"] > s["version"] and all(c["dutyCode"] for c in after["cells"])
    assert after["readiness"]["hardViolations"] == 0


def test_fixed_cells_are_kept():
    h = ward_head(2, 2, 1)
    add_nurses(h, 13)
    s = h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    a, d = s["nurses"][0]["id"], s["cells"][0]["date"]
    h.data("PATCH", f"/wards/me/schedules/{s['id']}/cells",
           {"baseVersion": s["version"], "changes": [{"nurseId": a, "date": d, "dutyCode": "ED"}]})
    err(h("POST", f"/wards/me/schedules/{s['id']}/generations", {"maxSeconds": 999}), 400, "VALIDATION_ERROR")
    err(h("POST", f"/wards/me/schedules/{s['id']}/generations", {"fixedCells": [{"nurseId": a, "date": "2020-01-01"}]}),
        422, "PRECONDITION_FAILED")
    h.data("POST", f"/wards/me/schedules/{s['id']}/generations", {"fixedCells": [{"nurseId": a, "date": d}]})
    cells = h.data("GET", f"/wards/me/schedules/{s['id']}")["cells"]
    assert next(c for c in cells if c["nurseId"] == a and c["date"] == d)["dutyCode"] == "ED"


def test_no_solution_partial_result_and_relaxation():
    h = ward_head(3, 3, 3)  # 수간호사 1명뿐
    s = h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    short = err(h("POST", f"/wards/me/schedules/{s['id']}/generations", {"maxSeconds": 10}), 422, "STAFFING_SHORTAGE")
    assert short["details"]["nursesNeeded"] > 0 and short["details"]["shortDays"] > 0
    job = h.data("POST", f"/wards/me/schedules/{s['id']}/generations", {"maxSeconds": 10, "allowShortage": True})
    assert job["status"] == "NO_SOLUTION" and job["partialResultAvailable"]
    assert {c["ruleCode"] for c in job["conflicts"]} == {"COVERAGE"}
    assert [x["id"] for x in job["relaxations"]] == ["SOFTEN_COVERAGE"]
    assert h.data("GET", f"/wards/me/schedules/{s['id']}")["version"] == s["version"], "해가 없으면 근무표는 그대로"
    err(h("POST", f"/wards/me/generations/{job['id']}/relaxations", {"relaxationIds": ["SOFTEN_X"]}),
        422, "RELAXATION_NOT_ALLOWED")
    url = f"/wards/me/generations/{job['id']}/partial-result/apply"
    err(h("POST", url, {"baseVersion": s["version"] - 1}), 409, "VERSION_CONFLICT")
    r = h.data("POST", url, {"baseVersion": s["version"]})
    assert r["schedule"]["version"] == s["version"] + 1 and r["violations"]
    err(h("POST", url, {"baseVersion": r["schedule"]["version"]}), 404, "PARTIAL_RESULT_NOT_FOUND")
    new = h.data("POST", f"/wards/me/generations/{job['id']}/relaxations", {"relaxationIds": ["SOFTEN_COVERAGE"]},
                 status=202)
    assert new["status"] == "SUCCEEDED" and new["id"] != job["id"]
    cov = next(r for r in h.data("GET", "/wards/me/rules") if r["code"] == "COVERAGE")
    assert cov["severity"] == "SOFT"
    err(ward_head()("GET", f"/wards/me/generations/{job['id']}"), 404, "JOB_NOT_FOUND")


def test_surplus_fixes_daily_staffing_and_carries_cut_offs():
    """인력이 남으면 날마다 같은 적정 인원(올림)으로 고정하고, 모자란 근무일만큼 덜 준 O는 확정 후 다음 달로 이월 (11/23)"""
    h = ward_head(2, 2, 1)
    add_nurses(h, 13)
    s = h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    job = h.data("POST", f"/wards/me/schedules/{s['id']}/generations", {"maxSeconds": 15}, status=202)
    assert job["status"] == "SUCCEEDED"
    cov = h.data("GET", f"/wards/me/schedules/{s['id']}/coverage")
    for duty in "DEN":
        rows = [c for c in cov if c["dutyCode"] == duty]
        assert len({c["requiredCount"] for c in rows}) == 1, f"{duty} 적정 인원은 날마다 같다"
        assert all(c["requiredCount"] >= c["minimumCount"] for c in rows)
    assert {c["dutyCode"] for c in cov if c["requiredCount"] > c["minimumCount"]} <= {"D", "E"}, "N은 올리지 않는다"
    assert all(c["actualCount"] == c["requiredCount"] for c in cov), [c for c in cov if c["status"] != "MET"][:3]
    s = h.data("GET", f"/wards/me/schedules/{s['id']}")
    shift = {n["id"] for n in s["nurses"] if n["role"] != "HEAD_NURSE"}
    left = {x["nurseId"]: x["offTarget"] - x["O"] for x in s["statistics"] if x["nurseId"] in shift}
    assert max(left.values()) - min(left.values()) <= 1 and min(left.values()) >= 0, "덜 주는 O는 사람마다 1개 차이까지"
    confirm_schedule(h, s)
    after = (NEXT + timedelta(days=32)).strftime("%Y-%m")
    nxt = h.data("POST", "/wards/me/schedules", {"yearMonth": after}, status=201)
    assert {x["nurseId"]: x["offCarry"] for x in nxt["statistics"] if x["nurseId"] in shift} == left
