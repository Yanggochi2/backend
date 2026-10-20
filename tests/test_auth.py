"""API-AUTH-01~05 + 공통 규약 (오류 형식·CSRF·쿠키)"""
from conftest import Api, err, user


def test_signup_validates_and_ignores_role():
    a = Api()
    e = err(a("POST", "/auth/signup", {"name": "x", "email": "weak@x.com", "password": "short", "termsAgreed": True}),
            400, "VALIDATION_ERROR")
    assert {"field": "password", "reason": "WEAK_PASSWORD"} in e["fieldErrors"]
    e = err(a("POST", "/auth/signup", {"name": "x", "email": "t@x.com", "password": "pass1234", "termsAgreed": False}),
            400, "VALIDATION_ERROR")
    assert {"field": "termsAgreed", "reason": "TERMS_NOT_AGREED"} in e["fieldErrors"]
    d = a.data("POST", "/auth/signup", {"name": "김수간", "email": "auth@x.com", "password": "pass1234",
                                        "termsAgreed": True, "role": "HEAD_NURSE", "wardId": "x"}, status=201)
    assert set(d) == {"id", "name", "email", "accountStatus"} and d["accountStatus"] == "ACTIVE"
    err(a("POST", "/auth/signup", {"name": "a", "email": "AUTH@x.com", "password": "pass1234", "termsAgreed": True}),
        409, "EMAIL_ALREADY_EXISTS")


def test_login_sets_cookies_and_me():
    a = Api()
    a("POST", "/auth/signup", {"name": "로그인", "email": "login@x.com", "password": "pass1234", "termsAgreed": True})
    err(a("GET", "/me"), 401, "UNAUTHENTICATED")
    err(a("POST", "/auth/login", {"email": "login@x.com", "password": "wrong1234"}), 401, "INVALID_CREDENTIALS")
    r = a("POST", "/auth/login", {"email": "login@x.com", "password": "pass1234"})
    cookies = r.headers.get_list("set-cookie")
    assert any(c.startswith("ACCESS_TOKEN=") and "HttpOnly" in c and "SameSite=strict" in c for c in cookies)
    assert any(c.startswith("REFRESH_TOKEN=") and "Path=/api/v1/auth" in c for c in cookies)
    assert any(c.startswith("CSRF_TOKEN=") and "HttpOnly" not in c for c in cookies)
    me = a.data("GET", "/me")
    assert me["user"]["email"] == "login@x.com" and me["membership"] is None and me["ward"] is None


def test_state_change_requires_csrf_header():
    a = user()
    err(a("POST", "/auth/logout", headers={"X-CSRF-Token": "nope"}), 403, "CSRF_TOKEN_INVALID")


def test_refresh_rotates_tokens_and_logout_ends_session():
    a = user()
    old_access, old_refresh = a.c.cookies.get("ACCESS_TOKEN"), a.c.cookies.get("REFRESH_TOKEN")
    a.data("POST", "/auth/refresh")
    assert a.data("GET", "/me")
    err(Api(cookies={"ACCESS_TOKEN": old_access})("GET", "/me"), 401, "UNAUTHENTICATED")
    stale = Api(cookies={"REFRESH_TOKEN": old_refresh, "CSRF_TOKEN": "t"})
    err(stale("POST", "/auth/refresh"), 401, "REFRESH_TOKEN_INVALID")
    assert a("POST", "/auth/logout").status_code == 204
    err(a("GET", "/me"), 401, "UNAUTHENTICATED")


def test_unknown_route_uses_error_format():
    r = Api()("GET", "/nope")
    err(r, 404, "RESOURCE_NOT_FOUND")
    assert r.headers["X-Trace-Id"] == r.json()["error"]["traceId"]
