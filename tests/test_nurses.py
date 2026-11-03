"""API-NUR-01~05 간호사 관리"""
from conftest import NURSE, add_nurses, err, member, ward_head


def test_register_and_validate():
    h = ward_head()
    [a] = add_nurses(h, 1)
    err(h("POST", "/wards/me/nurses", {**NURSE, "name": "간호사00"}), 409, "NURSE_ALREADY_EXISTS")
    err(h("POST", "/wards/me/nurses", {**NURSE, "name": "x", "skillLevel": 6}), 400, "VALIDATION_ERROR")
    err(h("POST", "/wards/me/nurses", {**NURSE, "name": "x", "status": "RETIRED"}), 422, "BUSINESS_RULE_VIOLATION")
    err(h("POST", "/wards/me/nurses", {**NURSE, "name": "프리", "isPreceptor": True, "preceptorOf": [a]}), 422, "INVALID_PRECEPTEE")
    [new] = add_nurses(h, 1, "신입", dutyRole="NEW")
    pre = h.data("POST", "/wards/me/nurses", {**NURSE, "name": "프리", "dutyRole": "PRECEPTOR", "preceptorOf": [new]})
    assert pre["preceptorOf"] == [new]


def test_patch_partial_with_version():
    h = ward_head()
    [a] = add_nurses(h, 1)
    e = err(h("PATCH", f"/wards/me/nurses/{a}", {"role": "HEAD_NURSE"}), 400, "VALIDATION_ERROR")
    assert e["fieldErrors"] == [{"field": "role", "reason": "NOT_ALLOWED"}]
    err(h("PATCH", f"/wards/me/nurses/{a}", {"name": None}), 400, "VALIDATION_ERROR")
    v = h.data("GET", f"/wards/me/nurses/{a}")["version"]
    r = h.data("PATCH", f"/wards/me/nurses/{a}", {"skillLevel": 5, "version": v})
    assert r["nurse"]["skillLevel"] == 5 and r["nurse"]["name"] == "간호사00" and "violations" in r
    err(h("PATCH", f"/wards/me/nurses/{a}", {"skillLevel": 4, "version": v}), 409, "VERSION_CONFLICT")


def test_list_filter_sort_and_page():
    h = ward_head()
    ids = add_nurses(h, 3)
    h.data("PATCH", f"/wards/me/nurses/{ids[2]}", {"careerMonths": 99})
    p = h("GET", "/wards/me/nurses?sort=proficiency&size=2").json()
    assert p["data"][1]["id"] == ids[2] and p["meta"]["totalElements"] == 4 and p["meta"]["totalPages"] == 2
    assert [x["id"] for x in h.data("GET", "/wards/me/nurses?q=간호사01")] == [ids[1]]


def test_retire():
    h = ward_head()
    [a] = add_nurses(h, 1)
    me = h.workspace["headNurseId"]
    err(h("POST", f"/wards/me/nurses/{me}/retire", {"affiliationEnd": "2030-01-01"}), 409, "LAST_HEAD_NURSE")
    err(h("POST", f"/wards/me/nurses/{a}/retire", {"affiliationEnd": "2020-01-01"}), 422, "INVALID_END_DATE")
    assert h.data("POST", f"/wards/me/nurses/{a}/retire", {"affiliationEnd": "2030-01-01"})["nurse"]["status"] == \
        "RETIRED"
    assert all(x["id"] != a for x in h.data("GET", "/wards/me/nurses"))
    assert any(x["id"] == a for x in h.data("GET", "/wards/me/nurses?includeRetired=true"))


def test_delete_soft_deletes_and_preserves_row():
    h = ward_head()
    [nurse_id] = add_nurses(h, 1)
    assert h("DELETE", f"/wards/me/nurses/{nurse_id}").status_code == 204
    assert all(x["id"] != nurse_id for x in h.data("GET", "/wards/me/nurses"))
    deleted = next(x for x in h.data("GET", "/wards/me/nurses?includeRetired=true") if x["id"] == nurse_id)
    assert deleted["status"] == "RETIRED" and deleted["affiliationEnd"]


def test_other_ward_nurse_is_hidden():
    h, other = ward_head(), ward_head()
    [a] = add_nurses(h, 1)
    err(other("GET", f"/wards/me/nurses/{a}"), 404, "NURSE_NOT_FOUND")
    err(other("PATCH", f"/wards/me/nurses/{a}", {"skillLevel": 1}), 404, "NURSE_NOT_FOUND")


def test_year_only_and_preceptor_is_not_rank():
    h = ward_head()
    for year in (True, 1899, 9999, "2020"):
        err(h("POST", "/wards/me/nurses", {**NURSE, "name": "입사", "joinedYear": year}), 400, "VALIDATION_ERROR")
    ordinary = h.data("POST", "/wards/me/nurses", {**NURSE, "name": "가일반", "careerMonths": 300})
    preceptor = h.data("POST", "/wards/me/nurses", {**NURSE, "name": "나프리", "isPreceptor": True, "careerMonths": 60})
    charge = h.data("POST", "/wards/me/nurses", {**NURSE, "name": "다차지", "dutyRole": "CHARGE", "careerMonths": 276})
    assert preceptor["dutyRole"] == "GENERAL" and preceptor["isPreceptor"]
    assert [n["id"] for n in h.data("GET", "/wards/me/nurses")] == [h.workspace["headNurseId"], charge["id"], ordinary["id"], preceptor["id"]]
    assert h.data("GET", "/wards/me/nurses?sort=name")[0]["id"] == ordinary["id"]
    err(h("GET", "/wards/me/nurses?sort=careerMonths,desc"), 400, "INVALID_SORT")
    result = h.data("PATCH", f"/wards/me/nurses/{ordinary['id']}", {"joinedYear": 2001})["nurse"]
    assert result["joinedYear"] == 2001 and "joinedAt" not in result and result["affiliationStart"] == "2025-01-01"
