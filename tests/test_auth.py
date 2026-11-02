"""로그인 제거와 편집 capability 경계."""
import uuid
import pytest
from conftest import Api, NURSE, err, ward_head
from app.main import app


@pytest.mark.parametrize("method,path", [
    ("POST", "/auth/signup"), ("POST", "/auth/login"), ("POST", "/auth/refresh"), ("POST", "/auth/logout"),
    ("GET", "/me"), ("DELETE", "/me"), ("POST", "/wards"),
    ("POST", "/ward-membership-requests"), ("GET", "/wards/me/join-code"),
    ("POST", "/wards/me/membership/leave"), ("GET", "/me/notifications"),
    ("POST", f"/wards/me/nurses/{uuid.uuid4()}/invite-code"),
])
def test_removed_routes(method, path):
    err(Api()(method, path, {} if method != "GET" else None), 404, "RESOURCE_NOT_FOUND")
    assert path not in app.openapi()["paths"]


def test_management_requires_header_and_creates_no_login_cookie():
    h = ward_head()
    assert not h.c.cookies
    assert h.workspace["managementPath"].startswith("/manage#token=")
    for headers in ({}, {"Authorization": "Bearer invalid"}, {"Cookie": "ACCESS_TOKEN=old"}):
        err(Api()("GET", "/wards/me", headers=headers), 401, "MANAGEMENT_LINK_REQUIRED")
    assert h.data("POST", "/wards/me/nurses", {**NURSE, "name": "등록"})["name"] == "등록"


def test_rotation_revokes_old_management_link():
    h = ward_head()
    new = h.data("POST", "/wards/me/management-link")
    err(h("GET", "/wards/me"), 401, "MANAGEMENT_LINK_REQUIRED")
    h.c.headers["Authorization"] = "Bearer " + new["managementToken"]
    assert h.data("GET", "/wards/me")["id"] == h.workspace["ward"]["id"]


def test_post_idempotency_and_ward_isolation():
    h, other = ward_head(), ward_head()
    headers = {"Idempotency-Key": "register-one"}
    body = {**NURSE, "name": "멱등성"}
    first = h("POST", "/wards/me/nurses", body, headers=headers)
    again = h("POST", "/wards/me/nurses", body, headers=headers)
    assert first.json() == again.json() and again.headers["Idempotent-Replayed"] == "true"
    err(h("POST", "/wards/me/nurses", {**body, "name": "다름"}, headers=headers), 422, "IDEMPOTENCY_KEY_REUSED")
    err(other("GET", "/wards/me/nurses/" + first.json()["data"]["id"]), 404, "NURSE_NOT_FOUND")
