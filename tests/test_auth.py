"""API-AUTH-01~05 + 공통 규약 (오류 형식·CSRF·쿠키)"""
from conftest import Api, err, user

from app.business import auth


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


def test_signup_logs_in():
    a = Api()
    a.data("POST", "/auth/signup", {"name": "가입", "email": "signin@x.com", "password": "pass1234", "termsAgreed": True},
           status=201)
    me = a.data("GET", "/me")
    assert me["user"]["email"] == "signin@x.com" and me["membership"] is None


def test_signup_rate_limited_per_client():
    a = Api()
    for i in range(auth._signups.max):
        a.c.cookies.clear()
        a("POST", "/auth/signup", {"name": "x", "email": f"rl{i}@x.com", "password": "pass1234", "termsAgreed": True})
    err(a("POST", "/auth/signup", {"name": "x", "email": "rlx@x.com", "password": "pass1234", "termsAgreed": True}),
        429, "RATE_LIMITED")


def test_login_sets_cookies_and_me():
    Api()("POST", "/auth/signup", {"name": "로그인", "email": "login@x.com", "password": "pass1234",
                                   "termsAgreed": True})
    a = Api()
    err(a("GET", "/me"), 401, "UNAUTHENTICATED")
    err(a("POST", "/auth/login", {"email": "nobody@x.com", "password": "pass1234"}), 401, "INVALID_CREDENTIALS")
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


def test_csrf_token_is_bound_to_session():
    """다른 세션의 CSRF 토큰을 쿠키·헤더에 맞춰 넣어도(double submit 우회) 거부된다"""
    a, b = user(), user()
    forged = Api(cookies={"ACCESS_TOKEN": a.c.cookies.get("ACCESS_TOKEN"), "CSRF_TOKEN": b.c.cookies.get("CSRF_TOKEN")})
    err(forged("POST", "/auth/logout"), 403, "CSRF_TOKEN_INVALID")
    assert a.data("GET", "/me")


def test_refresh_rotates_tokens_and_logout_ends_session():
    a = user()
    old_access, old_refresh = a.c.cookies.get("ACCESS_TOKEN"), a.c.cookies.get("REFRESH_TOKEN")
    a.data("POST", "/auth/refresh")
    assert a.data("GET", "/me")
    err(Api(cookies={"ACCESS_TOKEN": old_access})("GET", "/me"), 401, "UNAUTHENTICATED")
    assert a("POST", "/auth/logout").status_code == 204
    err(a("GET", "/me"), 401, "UNAUTHENTICATED")


def test_reused_refresh_token_revokes_whole_family():
    """이미 회전된 리프레시 토큰이 다시 오면(탈취) 최신 세션까지 모두 폐기한다"""
    a = user()
    stolen = Api(cookies={k: a.c.cookies.get(k) for k in ("REFRESH_TOKEN", "CSRF_TOKEN")})
    a.data("POST", "/auth/refresh")
    err(stolen("POST", "/auth/refresh"), 401, "REFRESH_TOKEN_INVALID")
    err(a("GET", "/me"), 401, "UNAUTHENTICATED")
    err(a("POST", "/auth/refresh"), 403, "CSRF_TOKEN_INVALID")  # 세션이 없으니 CSRF도 무효


def test_logout_with_expired_access_revokes_refresh():
    a = user()
    a.c.cookies.delete("ACCESS_TOKEN")  # 브라우저가 만료된 액세스 쿠키를 버린 상태
    err(a("GET", "/me"), 401, "UNAUTHENTICATED")
    refresh = a.c.cookies.get("REFRESH_TOKEN")
    assert a("POST", "/auth/logout").status_code == 204
    err(Api(cookies={"REFRESH_TOKEN": refresh, "CSRF_TOKEN": "t"})("POST", "/auth/refresh"), 403, "CSRF_TOKEN_INVALID")


def test_unknown_route_uses_error_format():
    r = Api()("GET", "/nope")
    err(r, 404, "RESOURCE_NOT_FOUND")
    assert r.headers["X-Trace-Id"] == r.json()["error"]["traceId"]
