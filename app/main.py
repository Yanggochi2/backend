"""병동 근무표 서비스 백엔드. 실행: uv run uvicorn app.main:app --reload"""
import asyncio
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.business import notifications
from app.business.common import KST, ApiException, iso
from app.persistence import models  # noqa: F401 (테이블 등록)
from app.persistence.db import Base, SessionLocal, engine, now
from app.presentation.routes import api

log = logging.getLogger("nurs")


def _remind() -> None:
    with SessionLocal() as db:
        notifications.remind_tomorrow(db)
        db.commit()


async def _reminder_loop() -> None:
    """근무 전날 리마인드: 매일 18시(KST). 테스트에서는 REMINDER_EVERY_SECONDS로 주기를 줄인다"""
    every = os.getenv("REMINDER_EVERY_SECONDS")
    while True:
        if every:
            wait = int(every)
        else:
            now = datetime.now(KST)
            nxt = now.replace(hour=18, minute=0, second=0, microsecond=0)
            wait = ((nxt if nxt > now else nxt + timedelta(days=1)) - now).total_seconds()
        await asyncio.sleep(wait)
        try:
            await run_in_threadpool(_remind)
        except Exception:
            log.exception("리마인드 실패")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # ponytail: 시작 시 테이블 자동 생성. 운영 배포 전 Alembic 마이그레이션으로 전환 (Yanggochi2/backend#24)
    Base.metadata.create_all(engine)
    task = asyncio.create_task(_reminder_loop())
    yield
    task.cancel()


app = FastAPI(title="병동 근무표 API", version="1.0", lifespan=lifespan)
app.include_router(api)


@app.middleware("http")
async def trace(request: Request, call_next):
    """모든 응답에 추적 ID. 오류 본문의 traceId와 같다"""
    request.state.trace_id = uuid.uuid4().hex
    response = await call_next(request)
    response.headers["X-Trace-Id"] = request.state.trace_id
    return response


def _error(request: Request, status: int, code: str, message: str, details=None, field_errors=None) -> JSONResponse:
    """1.3 오류 형식 {"error": {code, message, traceId, ...}}"""
    body = {"code": code, "message": message, "traceId": getattr(request.state, "trace_id", None), "status": status,
            "timestamp": iso(now())}
    if field_errors:
        body["fieldErrors"] = field_errors
    if details is not None:
        body["details"] = details
    return JSONResponse(jsonable_encoder({"error": body}), status_code=status)


@app.exception_handler(ApiException)
async def api_error(request: Request, e: ApiException):
    return _error(request, e.status, e.code, e.message, e.details, e.field_errors)


_REASONS = {"missing": "REQUIRED", "extra_forbidden": "NOT_ALLOWED", "enum": "INVALID_VALUE",
            "literal_error": "INVALID_VALUE", "string_too_short": "TOO_SHORT", "string_too_long": "TOO_LONG",
            "too_short": "TOO_SHORT", "too_long": "TOO_LONG", "json_invalid": "INVALID_JSON"}


@app.exception_handler(RequestValidationError)
async def invalid(request: Request, e: RequestValidationError):
    """깨진 JSON·필수값 누락·형식 오류·잘못된 enum 파라미터 → 400 VALIDATION_ERROR + 필드별 사유 코드"""
    fields = []
    for err in e.errors():
        loc = [x for x in err["loc"] if x not in ("body", "query", "path", "header", "cookie")]
        field = "".join(f"[{x}]" if isinstance(x, int) else f".{x}" for x in loc).lstrip(".") or "body"
        msg = err["msg"].removeprefix("Value error, ")
        if err["type"] in _REASONS:
            reason = _REASONS[err["type"]]
        elif err["type"].startswith(("greater", "less")):
            reason = "OUT_OF_RANGE"
        else:
            reason = msg if re.fullmatch(r"[A-Z_]+", msg) else "INVALID_FORMAT"
        fields.append({"field": field, "reason": reason})
    return _error(request, 400, "VALIDATION_ERROR", "요청 값을 확인해 주세요.", field_errors=fields)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, e: StarletteHTTPException):
    code = {404: "RESOURCE_NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(e.status_code, "HTTP_ERROR")
    return _error(request, e.status_code, code, str(e.detail))


@app.exception_handler(StaleDataError)
async def stale(request: Request, e: StaleDataError):
    """낙관적 잠금 충돌: 다른 요청이 같은 행을 먼저 저장함"""
    return _error(request, 409, "VERSION_CONFLICT", "다른 사용자가 먼저 수정했습니다. 새로고침 후 다시 시도하세요")


@app.exception_handler(IntegrityError)
async def integrity(request: Request, e: IntegrityError):
    """unique 제약 경쟁 (동시 가입 같은 이메일, 같은 달 근무표 동시 생성, 같은 Idempotency-Key 동시 요청 등)"""
    return _error(request, 409, "RESOURCE_ALREADY_EXISTS", "이미 존재하는 데이터입니다")


@app.exception_handler(Exception)
async def internal(request: Request, e: Exception):
    log.exception("처리되지 않은 오류 trace=%s", getattr(request.state, "trace_id", None))
    return _error(request, 500, "INTERNAL_ERROR", "서버 오류가 발생했습니다")
