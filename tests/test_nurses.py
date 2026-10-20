"""API-NUR-01~05 간호사 관리"""
from conftest import NURSE, add_nurses, err, member, ward_head


def test_register_and_validate():
    h = ward_head()
    [a] = add_nurses(h, 1)
    err(h("POST", "/wards/me/nurses", {**NURSE, "name": "간호사00"}), 409, "NURSE_ALREADY_EXISTS")
    err(h("POST", "/wards/me/nurses", {**NURSE, "name": "x", "skillLevel": 6}), 400, "VALIDATION_ERROR")
    err(h("POST", "/wards/me/nurses", {**NURSE, "name": "x", "status": "RETIRED"}), 422, "BUSINESS_RULE_VIOLATION")
    err(h("POST", "/wards/me/nurses", {**NURSE, "name": "프리", "preceptorOf": [a]}), 422, "INVALID_PRECEPTEE")
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


def test_nurse_sees_only_own_sensitive_fields():
    h = ward_head()
    [a] = add_nurses(h, 1)
    n, mine = member(h)
    rows = n("GET", "/wards/me/nurses?size=100").json()["data"]
    other = next(x for x in rows if x["id"] == a)
    me = next(x for x in rows if x["id"] == mine)
    assert other["skillLevel"] is None and other["status"] is None and other["careerMonths"] is None
    assert me["skillLevel"] is not None
    err(n("GET", "/wards/me/nurses?status=PREGNANT"), 400, "INVALID_FILTER")
    err(n("GET", "/wards/me/nurses?sort=careerMonths,desc"), 400, "INVALID_FILTER")
    err(n("GET", "/wards/me/nurses?size=101"), 400, "VALIDATION_ERROR")
    err(n("PATCH", f"/wards/me/nurses/{a}", {"skillLevel": 1}), 403, "FORBIDDEN")


def test_list_filter_sort_and_page():
    h = ward_head()
    ids = add_nurses(h, 3)
    h.data("PATCH", f"/wards/me/nurses/{ids[2]}", {"careerMonths": 99})
    p = h("GET", "/wards/me/nurses?sort=careerMonths,desc&size=2").json()
    assert p["data"][0]["id"] == ids[2] and p["meta"]["totalElements"] == 4 and p["meta"]["totalPages"] == 2
    assert [x["id"] for x in h.data("GET", "/wards/me/nurses?q=간호사01")] == [ids[1]]


def test_retire():
    h = ward_head()
    [a] = add_nurses(h, 1)
    me = h.data("GET", "/me")["membership"]["id"]
    err(h("POST", f"/wards/me/nurses/{me}/retire", {"affiliationEnd": "2030-01-01"}), 409, "LAST_HEAD_NURSE")
    err(h("POST", f"/wards/me/nurses/{a}/retire", {"affiliationEnd": "2020-01-01"}), 422, "INVALID_END_DATE")
    assert h.data("POST", f"/wards/me/nurses/{a}/retire", {"affiliationEnd": "2030-01-01"})["nurse"]["status"] == \
        "RETIRED"
    assert all(x["id"] != a for x in h.data("GET", "/wards/me/nurses"))
    assert any(x["id"] == a for x in h.data("GET", "/wards/me/nurses?includeRetired=true"))


def test_other_ward_nurse_is_hidden():
    h, other = ward_head(), ward_head()
    [a] = add_nurses(h, 1)
    err(other("GET", f"/wards/me/nurses/{a}"), 404, "NURSE_NOT_FOUND")
    err(other("PATCH", f"/wards/me/nurses/{a}", {"skillLevel": 1}), 404, "NURSE_NOT_FOUND")
