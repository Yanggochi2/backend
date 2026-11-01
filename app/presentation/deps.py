"""요청 단위 트랜잭션, 편집 링크 권한, Idempotency-Key."""
import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Query, Request, Response
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.business import access
from app.business.common import ApiException, sha256
from app.persistence.db import SessionLocal, now
from app.persistence.models import IdempotencyRecord

PREFIX = "/api/v1"
IDEMPOTENCY_TTL = timedelta(hours=24)


class TxRoute(APIRoute):
    """
    요청마다 DB 세션을 열고, 엔드포인트가 끝나면 응답을 보내기 전에 커밋한다 (오류 시 롤백).
    커밋 단계의 unique 위반·버전 충돌도 409로 응답할 수 있게 하기 위함.
    편집 토큰은 Authorization 헤더로만 받는다. 쿠키 인증은 사용하지 않는다.
    POST에 Idempotency-Key가 있으면 같은 사용자·키·요청에 저장된 2xx 응답을 그대로 돌려준다
    """

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def tx(request: Request) -> Response:
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
    """저장된 응답(Response) 또는 저장할 때 쓸 (user_id, key, fingerprint). 편집 토큰이 없으면 None"""
    # 비밀 링크 응답은 멱등성 캐시에 원문을 남기지 않는다.
    if request.url.path.endswith(("/management-link", "/share-link")) or request.url.path == PREFIX + "/workspaces":
        return None
    try:
        user_id = access.resolve(db, request.headers.get("Authorization"))
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
    return access.resolve(db, request.headers.get("Authorization"))


UserId = Annotated[uuid.UUID, Depends(current_user)]


class Paging:
    def __init__(self, page: int = Query(0, ge=0), size: int = Query(20, ge=1, le=100)):
        self.page, self.size = page, size


Page = Annotated[Paging, Depends()]
