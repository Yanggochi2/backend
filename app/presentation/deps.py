"""요청 단위 트랜잭션, 인증 쿠키, CSRF, Idempotency-Key (1.1·1.5)"""
import os
import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Query, Request, Response
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.business import auth
from app.business.common import ApiException, sha256
from app.persistence.db import SessionLocal, now
from app.persistence.models import IdempotencyRecord

PREFIX = "/api/v1"
ACCESS, REFRESH, CSRF = "ACCESS_TOKEN", "REFRESH_TOKEN", "CSRF_TOKEN"
REFRESH_PATH = f"{PREFIX}/auth"  # 리프레시 토큰은 인증 경로에만 실려 가게 한다
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"
CSRF_EXEMPT = {f"{PREFIX}/auth/signup", f"{PREFIX}/auth/login"}  # 세션이 없는 공개 요청
IDEMPOTENCY_TTL = timedelta(hours=24)


class TxRoute(APIRoute):
    """
    요청마다 DB 세션을 열고, 엔드포인트가 끝나면 응답을 보내기 전에 커밋한다 (오류 시 롤백).
    커밋 단계의 unique 위반·버전 충돌도 409로 응답할 수 있게 하기 위함.
    상태 변경 요청은 X-CSRF-Token 헤더 = CSRF_TOKEN 쿠키 (double submit)를 요구한다.
    POST에 Idempotency-Key가 있으면 같은 사용자·키·요청에 저장된 2xx 응답을 그대로 돌려준다
    """

    def get_route_handler(self):
        handler = super().get_route_handler()
        csrf_required = self.path not in CSRF_EXEMPT

        async def tx(request: Request) -> Response:
            if request.method in ("POST", "PUT", "PATCH", "DELETE") and csrf_required:
                token = request.cookies.get(CSRF)
                if not token or request.headers.get("X-CSRF-Token") != token:
                    raise ApiException(403, "CSRF_TOKEN_INVALID", "CSRF 토큰이 없거나 일치하지 않습니다")
            db = SessionLocal()
            request.state.db = db
            try:
                idem = None
                if request.method == "POST" and (key := request.headers.get("Idempotency-Key")):
                    body = await request.body()
                    idem = await run_in_threadpool(_idempotency, db, request, key, body)
                    if isinstance(idem, Response):
                        return idem
                response = await handler(request)
                if idem and 200 <= response.status_code < 300:
                    user_id, key, fingerprint = idem
                    db.add(IdempotencyRecord(user_id=user_id, key=key, fingerprint=fingerprint,
                                             status_code=response.status_code, body=bytes(response.body)))
                await run_in_threadpool(db.commit)
                return response
            except BaseException:
                await run_in_threadpool(db.rollback)
                raise
            finally:
                await run_in_threadpool(db.close)

        return tx


def _idempotency(db: Session, request: Request, key: str, body: bytes):
    """저장된 응답(Response) 또는 저장할 때 쓸 (user_id, key, fingerprint). 비로그인이면 None"""
    try:
        user_id = auth.authenticate(db, request.cookies.get(ACCESS))
    except ApiException:
        return None
    fingerprint = sha256(f"{request.method} {request.url.path}?{request.url.query} " + body.hex())
    rec = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.user_id == user_id, IdempotencyRecord.key == key))
    if rec and rec.created_at + IDEMPOTENCY_TTL < now():
        db.delete(rec)
        db.flush()
        rec = None
    if rec is None:
        return user_id, key, fingerprint
    if rec.fingerprint != fingerprint:
        raise ApiException(422, "IDEMPOTENCY_KEY_REUSED", "같은 Idempotency-Key를 다른 요청에 사용했습니다")
    return Response(rec.body, rec.status_code, media_type="application/json", headers={"Idempotent-Replayed": "true"})


def get_db(request: Request) -> Session:
    return request.state.db


DB = Annotated[Session, Depends(get_db)]


def current_user(request: Request, db: DB) -> uuid.UUID:
    return auth.authenticate(db, request.cookies.get(ACCESS))


UserId = Annotated[uuid.UUID, Depends(current_user)]


class Paging:
    def __init__(self, page: int = Query(0, ge=0), size: int = Query(20, ge=1, le=100)):
        self.page, self.size = page, size


Page = Annotated[Paging, Depends()]


def set_auth_cookies(response: Response, tokens: dict) -> None:
    """토큰은 HttpOnly·Secure·SameSite 쿠키로만 주고받는다. CSRF 토큰만 스크립트가 읽어 헤더로 보낸다"""
    common = {"secure": COOKIE_SECURE, "samesite": "strict"}
    response.set_cookie(ACCESS, tokens["access"], httponly=True, path="/",
                        max_age=int(auth.ACCESS_TTL.total_seconds()), **common)
    response.set_cookie(REFRESH, tokens["refresh"], httponly=True, path=REFRESH_PATH,
                        max_age=int(auth.REFRESH_TTL.total_seconds()), **common)
    response.set_cookie(CSRF, tokens["csrf"], httponly=False, path="/",
                        max_age=int(auth.REFRESH_TTL.total_seconds()), **common)


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS, path="/")
    response.delete_cookie(REFRESH, path=REFRESH_PATH)
    response.delete_cookie(CSRF, path="/")
