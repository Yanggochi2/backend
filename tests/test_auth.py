"""수간호사 계정 인증과 일반 간호사 읽기 전용 경계."""
import uuid

import pytest

from app.business import access
from app.business.common import ApiException
from app.main import app
from app.persistence.db import SessionLocal
from app.persistence.models import Nurse, User
from conftest import Api, NURSE, add_nurses, err, user, ward_head


@pytest.mark.parametrize("method,path", [
    ("POST", "/ward-membership-requests"), ("GET", "/wards/me/join-code"),
    ("POST", "/wards/me/membership/leave"), ("GET", "/me/notifications"),
    ("POST", f"/wards/me/nurses/{uuid.uuid4()}/invite-code"),
])
def test_general_membership_and_invite_routes_are_removed(method, path):
    err(Api()(method, path, {} if method != "GET" else None), 404, "RESOURCE_NOT_FOUND")
    assert path not in app.openapi()["paths"]


def test_head_uses_own_login_account_for_management():
    h = ward_head()
    assert h.c.cookies.get("ACCESS_TOKEN")
    assert h.data("GET", "/me")["membership"]["role"] == "HEAD_NURSE"
    assert h.data("POST", "/wards/me/nurses", {**NURSE, "name": "등록"})["name"] == "등록"

    h("POST", "/auth/logout")
    err(h("GET", "/wards/me"), 401, "UNAUTHENTICATED")
    h.data("POST", "/auth/login", {"email": h.email, "password": h.password})
    assert h.data("GET", "/wards/me")["id"] == h.workspace["ward"]["id"]


def test_account_without_head_role_cannot_use_management_api():
    h = ward_head()
    [nurse_id] = add_nurses(h, 1)
    general = user("일반 간호사")
    with SessionLocal() as db:
        nurse = db.get(Nurse, uuid.UUID(nurse_id))
        nurse.user_id = uuid.UUID(general.account["id"])
        db.commit()
    err(general("GET", "/wards/me"), 403, "FORBIDDEN")
    err(general("POST", "/wards/me/schedules", {"yearMonth": "2028-01"}), 403, "FORBIDDEN")


def test_unauthenticated_user_cannot_use_management_api():
    err(Api()("GET", "/wards/me"), 401, "UNAUTHENTICATED")
    err(Api()("POST", "/workspaces", {}), 401, "UNAUTHENTICATED")


def test_passwords_preserve_whitespace_and_reject_invalid_bcrypt_lengths():
    client = Api()
    email = f"{uuid.uuid4()}@example.com"
    password = " Password123! "
    client.data("POST", "/auth/signup", {"name": "수간호사", "email": email, "password": password,
                                           "termsAgreed": True}, status=201)
    client("POST", "/auth/logout")
    err(client("POST", "/auth/login", {"email": email, "password": password.strip()}),
        401, "INVALID_CREDENTIALS")
    client.data("POST", "/auth/login", {"email": email, "password": password})

    for invalid in ("   ", "가" * 25, "a" * 73):
        response = Api()("POST", "/auth/login", {"email": email, "password": invalid})
        err(response, 400, "VALIDATION_ERROR")


def test_existing_synthetic_head_can_be_associated_with_real_account():
    h = ward_head()
    replacement = user("기존 병동 수간호사")
    ward_id = uuid.UUID(h.workspace["ward"]["id"])
    head_id = uuid.UUID(h.workspace["headNurseId"])
    with SessionLocal() as db:
        head = db.get(Nurse, head_id)
        synthetic = db.get(User, head.user_id)
        synthetic.email = f"{uuid.uuid4()}@workspace.invalid"
        synthetic.password_hash = "!"
        result = access.associate_head_account(db, ward_id, replacement.email)
        db.commit()
    assert result["headNurseId"] == head_id and not result["alreadyLinked"]
    assert replacement.data("GET", "/wards/me")["id"] == str(ward_id)

    with SessionLocal() as db:
        fake = User(name="합성", email=f"{uuid.uuid4()}@workspace.invalid", password_hash="!")
        db.add(fake)
        db.flush()
        with pytest.raises(ApiException) as raised:
            access.associate_head_account(db, ward_id, fake.email)
        assert raised.value.status == 422 and raised.value.code == "ACCOUNT_NOT_ELIGIBLE"
        db.rollback()


def test_post_idempotency_and_ward_isolation():
    h, other = ward_head(), ward_head()
    headers = {"Idempotency-Key": "register-one"}
    body = {**NURSE, "name": "멱등성"}
    first = h("POST", "/wards/me/nurses", body, headers=headers)
    again = h("POST", "/wards/me/nurses", body, headers=headers)
    assert first.json() == again.json() and again.headers["Idempotent-Replayed"] == "true"
    err(h("POST", "/wards/me/nurses", {**body, "name": "다름"}, headers=headers),
        422, "IDEMPOTENCY_KEY_REUSED")
    err(other("GET", "/wards/me/nurses/" + first.json()["data"]["id"]), 404, "NURSE_NOT_FOUND")
