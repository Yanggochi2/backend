"""개인 초대 코드: 미리 등록한 간호사 행에 계정을 승인 없이 연결"""
from datetime import timedelta

from sqlalchemy import update

from conftest import NEXT, add_nurses, err, member, user, ward_head
from app.persistence.db import SessionLocal, now
from app.persistence.models import Nurse


def test_invite_links_account_to_registered_row():
    h = ward_head()
    [kim] = add_nurses(h, 1, "김간호")
    n = user("김간호")
    err(n("POST", f"/wards/me/nurses/{kim}/invite-code"), 404, "WARD_NOT_FOUND")
    inv = h.data("POST", f"/wards/me/nurses/{kim}/invite-code", status=201)
    assert len(inv["code"]) == 10 and h.data("GET", f"/wards/me/nurses/{kim}")["inviteExpiresAt"]

    r = n.data("POST", "/ward-membership-requests", {"joinCode": inv["code"].lower()}, status=201)
    assert r["status"] == "APPROVED" and r["via"] == "INVITE_CODE"
    assert n.data("GET", "/me")["membership"]["id"] == kim, "미리 등록한 행에 연결"
    assert h("GET", "/wards/me/membership-requests?status=PENDING").json()["meta"]["totalElements"] == 0
    # 신청이 그 행에 붙는다
    req = n.data("POST", "/wards/me/requests", {"type": "PREFERRED_OFF", "targetDates": [NEXT.isoformat()],
                                                "reasonCode": "PERSONAL"})
    assert req["applicantId"] == kim
    # 1회용 · 연결된 행은 재발급 불가
    err(user()("POST", "/ward-membership-requests", {"joinCode": inv["code"]}), 404, "JOIN_CODE_NOT_FOUND")
    err(h("POST", f"/wards/me/nurses/{kim}/invite-code"), 409, "NURSE_ALREADY_LINKED")
    assert h.data("GET", f"/wards/me/nurses/{kim}")["hasAccount"] is True


def test_reissue_invalidates_old_code_and_expiry():
    h = ward_head()
    [a] = add_nurses(h, 1)
    old = h.data("POST", f"/wards/me/nurses/{a}/invite-code")["code"]
    new = h.data("POST", f"/wards/me/nurses/{a}/invite-code")["code"]
    n = user()
    err(n("POST", "/ward-membership-requests", {"joinCode": old}), 404, "JOIN_CODE_NOT_FOUND")
    with SessionLocal() as db:
        db.execute(update(Nurse).where(Nurse.invite_code == new).values(invite_expires_at=now() - timedelta(1)))
        db.commit()
    err(n("POST", "/ward-membership-requests", {"joinCode": new}), 422, "INVITE_CODE_EXPIRED")


def test_invite_cancels_pending_ward_code_request():
    h, other = ward_head(), ward_head()
    [a] = add_nurses(h, 1)
    n = user()
    n.data("POST", "/ward-membership-requests", {"joinCode": other.data("GET", "/wards/me/join-code")["code"]})
    n.data("POST", "/ward-membership-requests",
           {"joinCode": h.data("POST", f"/wards/me/nurses/{a}/invite-code")["code"]})
    assert other("GET", "/wards/me/membership-requests?status=PENDING").json()["meta"]["totalElements"] == 0
    err(n("POST", "/ward-membership-requests", {"joinCode": "ABCDEFGH"}), 409, "MEMBERSHIP_ALREADY_EXISTS")


def test_invite_rules():
    h = ward_head()
    n, nid = member(h)
    err(n("POST", f"/wards/me/nurses/{nid}/invite-code"), 403, "FORBIDDEN")
    err(ward_head()("POST", f"/wards/me/nurses/{nid}/invite-code"), 404, "NURSE_NOT_FOUND")
    [a] = add_nurses(h, 1)
    code = h.data("POST", f"/wards/me/nurses/{a}/invite-code")["code"]
    h.data("POST", f"/wards/me/nurses/{a}/retire", {"affiliationEnd": "2030-01-01"})
    err(user()("POST", "/ward-membership-requests", {"joinCode": code}), 404, "JOIN_CODE_NOT_FOUND")
    err(h("POST", f"/wards/me/nurses/{a}/invite-code"), 422, "NURSE_NOT_ELIGIBLE")


def test_heads_are_notified_with_name_mismatch():
    """승인 단계가 없으므로 수간호사에게 알린다. 계정 이름이 다르면 확인을 요청한다"""
    h = ward_head()
    [kim, lee] = add_nurses(h, 2, "간호사")
    user("간호사00").data("POST", "/ward-membership-requests",
                         {"joinCode": h.data("POST", f"/wards/me/nurses/{kim}/invite-code")["code"]})
    user("다른사람").data("POST", "/ward-membership-requests",
                        {"joinCode": h.data("POST", f"/wards/me/nurses/{lee}/invite-code")["code"]})
    joined = [x for x in h("GET", "/me/notifications").json()["data"] if x["type"] == "MEMBER_JOINED"]
    bodies = sorted(x["body"] for x in joined)
    assert len(joined) == 2 and all(x["resourceId"] in (kim, lee) for x in joined)
    assert any("간호사00님이 초대 코드로 가입했습니다." == b for b in bodies), bodies
    assert any("'다른사람'" in b and "본인이 맞는지" in b for b in bodies), bodies


def test_revoke_invite():
    h = ward_head()
    [a] = add_nurses(h, 1)
    code = h.data("POST", f"/wards/me/nurses/{a}/invite-code")["code"]
    assert h("DELETE", f"/wards/me/nurses/{a}/invite-code").status_code == 204
    assert h.data("GET", f"/wards/me/nurses/{a}")["inviteExpiresAt"] is None
    err(user()("POST", "/ward-membership-requests", {"joinCode": code}), 404, "JOIN_CODE_NOT_FOUND")
    err(h("DELETE", f"/wards/me/nurses/{a}/invite-code"), 404, "INVITE_CODE_NOT_FOUND")
    n, _ = member(h)
    err(n("DELETE", f"/wards/me/nurses/{a}/invite-code"), 403, "FORBIDDEN")


def test_outstanding_invite_is_not_a_link_candidate():
    """초대 코드를 받은 사람이 따로 있는 행에 다른 신청자를 연결하지 못하게 한다"""
    h = ward_head()
    [a] = add_nurses(h, 1, "박간호")
    h.data("POST", f"/wards/me/nurses/{a}/invite-code")
    rid = user("박간호00").data("POST", "/ward-membership-requests",
                               {"joinCode": h.data("GET", "/wards/me/join-code")["code"]})["id"]
    [req] = h("GET", "/wards/me/membership-requests?status=PENDING").json()["data"]
    assert req["candidates"] == [], "유효한 초대 코드가 나간 행은 후보에서 제외"
    err(h("POST", f"/wards/me/membership-requests/{rid}/approve", {"nurseId": a}), 409, "INVITE_CODE_OUTSTANDING")
    # 코드를 취소하면 후보로 돌아오고 연결할 수 있다
    h("DELETE", f"/wards/me/nurses/{a}/invite-code")
    [req] = h("GET", "/wards/me/membership-requests?status=PENDING").json()["data"]
    assert req["candidates"] == [a]
    assert h.data("POST", f"/wards/me/membership-requests/{rid}/approve", {"nurseId": a})["id"] == a


def test_link_by_approval_clears_expired_invite():
    h = ward_head()
    [a] = add_nurses(h, 1, "최간호")
    code = h.data("POST", f"/wards/me/nurses/{a}/invite-code")["code"]
    with SessionLocal() as db:
        db.execute(update(Nurse).where(Nurse.invite_code == code).values(invite_expires_at=now() - timedelta(1)))
        db.commit()
    rid = user("최간호00").data("POST", "/ward-membership-requests",
                               {"joinCode": h.data("GET", "/wards/me/join-code")["code"]})["id"]
    h.data("POST", f"/wards/me/membership-requests/{rid}/approve", {"nurseId": a})
    assert h.data("GET", f"/wards/me/nurses/{a}")["inviteExpiresAt"] is None, "연결된 행에 만료 코드가 남지 않음"


def test_applicant_can_cancel_pending_request():
    """잘못된 병동에 신청해도 본인이 취소하고 다른 병동에 신청·개설할 수 있다"""
    wrong, right = ward_head(), ward_head()
    n = user()
    n.data("POST", "/ward-membership-requests", {"joinCode": wrong.data("GET", "/wards/me/join-code")["code"]})
    err(n("POST", "/ward-membership-requests", {"joinCode": right.data("GET", "/wards/me/join-code")["code"]}),
        409, "REQUEST_ALREADY_EXISTS")
    assert n("DELETE", "/ward-membership-requests/me").status_code == 204
    assert n.data("GET", "/me")["membershipRequest"]["status"] == "CANCELLED"
    assert wrong("GET", "/wards/me/membership-requests?status=PENDING").json()["meta"]["totalElements"] == 0
    err(n("DELETE", "/ward-membership-requests/me"), 404, "REQUEST_NOT_FOUND")
    n.data("POST", "/ward-membership-requests", {"joinCode": right.data("GET", "/wards/me/join-code")["code"]})
