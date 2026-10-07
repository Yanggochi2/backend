"""병동 근무표 서비스 백엔드. 실행: docker compose up --build"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.persistence.db import Base, engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    # ponytail: 시작 시 테이블 자동 생성. 운영 배포 전 Alembic 마이그레이션으로 전환 (Yanggochi2/backend#24)
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="병동 근무표 API", version="1.0", lifespan=lifespan)
