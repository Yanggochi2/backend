"""
API 라우트 (/api/v1). 병동 리소스는 /wards/me 아래에 두고 항상 세션 사용자의 소속 병동이 대상이다 (1.2).
컨트롤러는 요청·응답 변환만 하고 로직은 business 계층에 둔다. 단일 리소스는 {data}, 목록은 {data, meta}
"""

from fastapi import APIRouter, Request, Response

from app.business import (auth)
from app.presentation import schemas as s
from app.presentation.deps import (ACCESS, DB, PREFIX, REFRESH, TxRoute, UserId, clear_auth_cookies,
                                   set_auth_cookies)

api = APIRouter(prefix=PREFIX, route_class=TxRoute)


def _d(x) -> dict:
    return {"data": x}


# --- 인증 (AUTH-01·02)
@api.post("/auth/signup", status_code=201)
def signup(body: s.Signup, db: DB):
    return _d(auth.signup(db, body.name, body.email, body.password))


@api.post("/auth/login")
def login(body: s.Login, db: DB, response: Response):
    user, tokens = auth.login(db, body.email, body.password)
    set_auth_cookies(response, tokens)
    return _d(user)


@api.post("/auth/refresh")
def refresh(request: Request, db: DB, response: Response):
    user, tokens = auth.refresh(db, request.cookies.get(REFRESH))
    set_auth_cookies(response, tokens)
    return _d(user)


@api.post("/auth/logout", status_code=204)
def logout(user_id: UserId, db: DB, request: Request, response: Response):
    auth.logout(db, user_id, request.cookies.get(ACCESS))
    clear_auth_cookies(response)


@api.get("/me")
def me(user_id: UserId, db: DB):
    return _d(auth.me(db, user_id))
