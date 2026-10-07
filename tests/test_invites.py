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
