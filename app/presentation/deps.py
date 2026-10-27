"""요청 단위 트랜잭션과 단일 병동 실행 컨텍스트."""
import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Query, Request, Response
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.business.common import ApiException, not_found, sha256
from app.domain.model import NurseStatus, Role
from app.persistence.db import SessionLocal, now
from app.persistence.models import IdempotencyRecord, Nurse

PREFIX = "/api/v1"
IDEMPOTENCY_TTL = timedelta(hours=24)


class TxRoute(APIRoute):
    """
    요청마다 DB 세션을 열고, 엔드포인트가 끝나면 응답을 보내기 전에 커밋한다 (오류 시 롤백).
    커밋 단계의 unique 위반·버전 충돌도 409로 응답할 수 있게 하기 위함.
    로그인과 쿠키가 없는 단일 병동 도구이므로 CSRF 검사는 하지 않는다.
    POST에 Idempotency-Key가 있으면 같은 병동 관리자·키·요청에 저장된 2xx 응답을 그대로 돌려준다.
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
    """저장된 응답 또는 저장할 때 쓸 (관리자 id, key, fingerprint)."""
    user_id = current_user(request, db)
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
    """로그인 대신 실행 대상 병동의 수간호사를 내부 작업 주체로 사용한다."""
    query = select(Nurse).where(Nurse.role == Role.HEAD_NURSE, Nurse.status != NurseStatus.RETIRED)
    if raw := request.headers.get("X-Ward-Id"):
        try:
            query = query.where(Nurse.ward_id == uuid.UUID(raw))
        except ValueError:
            raise not_found("WARD_NOT_FOUND") from None
    operator = db.scalars(query.order_by(Nurse.affiliation_start, Nurse.id)).first()
    if operator is None or operator.user_id is None:
        raise not_found("WARD_NOT_FOUND")
    return operator.user_id


UserId = Annotated[uuid.UUID, Depends(current_user)]  # 내부 감사 로그용 작업 주체


class Paging:
    def __init__(self, page: int = Query(0, ge=0), size: int = Query(20, ge=1, le=100)):
        self.page, self.size = page, size


Page = Annotated[Paging, Depends()]
