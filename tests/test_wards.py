"""API-WARD-01~10 병동 개설·가입·권한"""
from conftest import err, member, user, ward_head


def test_create_ward():
    h = user()
    err(h("POST", "/wards", {"hospitalName": "한빛", "wardName": "7병동", "requiredStaff": {"D": 0, "E": 0, "N": 0},
                             "rulePreset": "STANDARD"}), 422, "INVALID_STAFFING")
    body = {"hospitalName": "한빛", "wardName": "7병동", "requiredStaff": {"D": 2, "E": 2, "N": 1},
            "rulePreset": "STANDARD"}
    r = h("POST", "/wards", body, headers={"Idempotency-Key": "ward-1"})
    assert r.status_code == 201
    d = r.json()["data"]
    assert d["membership"]["role"] == "HEAD_NURSE" and len(d["joinCode"]["code"]) == 8
    # 같은 Idempotency-Key + 같은 요청 → 저장된 응답, 다른 요청 → 422
    again = h("POST", "/wards", body, headers={"Idempotency-Key": "ward-1"})
    assert again.json() == r.json() and again.headers["Idempotent-Replayed"] == "true"
    err(h("POST", "/wards", {**body, "wardName": "8병동"}, headers={"Idempotency-Key": "ward-1"}),
        422, "IDEMPOTENCY_KEY_REUSED")
    err(h("POST", "/wards", body), 409, "MEMBERSHIP_ALREADY_EXISTS")
    w = h.data("GET", "/wards/me")
    assert w["requiredStaff"] == {"D": 2, "E": 2, "N": 1} and w["wardName"] == "7병동"
    me = h.data("GET", "/me")
    assert me["membership"]["role"] == "HEAD_NURSE" and me["ward"]["id"] == w["id"]


def test_join_with_code_and_approve():
    h = ward_head()
    old = h.data("GET", "/wards/me/join-code")["code"]
    code = h.data("POST", "/wards/me/join-code/rotate")["code"]
    assert code != old
    n = user("이간호")
    err(n("GET", "/wards/me"), 404, "WARD_NOT_FOUND")
    err(n("POST", "/ward-membership-requests", {"joinCode": old}), 404, "JOIN_CODE_NOT_FOUND")
    r = n.data("POST", "/ward-membership-requests", {"joinCode": code.lower()}, status=201)
    assert r["wardName"] == "7병동" and r["status"] == "PENDING"
    err(n("POST", "/ward-membership-requests", {"joinCode": code}), 409, "REQUEST_ALREADY_EXISTS")
    assert n.data("GET", "/me")["membershipRequest"]["status"] == "PENDING"
    err(n("GET", "/wards/me/membership-requests"), 404, "WARD_NOT_FOUND")
    page = h("GET", "/wards/me/membership-requests?status=PENDING").json()
    assert page["meta"] == {"page": 0, "size": 20, "totalElements": 1, "totalPages": 1}
    rid = page["data"][0]["id"]
    err(h("POST", f"/wards/me/membership-requests/{rid}/reject", {}), 400, "REASON_REQUIRED")
    m = h.data("POST", f"/wards/me/membership-requests/{rid}/approve")
    assert m["role"] == "NURSE"
    err(h("POST", f"/wards/me/membership-requests/{rid}/approve"), 409, "REQUEST_ALREADY_PROCESSED")
    err(n("GET", "/wards/me/join-code"), 403, "FORBIDDEN")
    assert n.data("GET", "/me")["membership"]["id"] == m["id"]


def test_reject_with_reason():
    h = ward_head()
    n = user()
    rid = n.data("POST", "/ward-membership-requests", {"joinCode": h.data("GET", "/wards/me/join-code")["code"]})["id"]
    r = h.data("POST", f"/wards/me/membership-requests/{rid}/reject", {"reason": "소속 아님"})
    assert r["status"] == "REJECTED" and r["rejectionReason"] == "소속 아님"


def test_grant_and_transfer_head_nurse():
    h = ward_head()
    me = h.data("GET", "/me")["membership"]["id"]
    n, nid = member(h)
    err(h("POST", "/wards/me/head-nurse-transfer", {"targetNurseId": me}), 409, "LAST_HEAD_NURSE_CONFLICT")
    assert h.data("POST", f"/wards/me/head-nurses/{nid}/grant")["role"] == "HEAD_NURSE"
    err(h("POST", f"/wards/me/head-nurses/{nid}/grant"), 409, "ROLE_ALREADY_ASSIGNED")
    t = n.data("POST", "/wards/me/head-nurse-transfer", {"targetNurseId": me})
    assert t["from"]["role"] == "NURSE" and t["to"]["role"] == "HEAD_NURSE"
    err(n("GET", "/wards/me/join-code"), 403, "FORBIDDEN")


def test_other_ward_ids_are_hidden():
    h, other = ward_head(), ward_head()
    _, nid = member(h)
    err(other("POST", f"/wards/me/head-nurses/{nid}/grant"), 404, "NURSE_NOT_FOUND")
