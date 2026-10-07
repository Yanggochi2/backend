import os
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# 운영: postgresql+psycopg://user:pw@host:5432/nurs  /  로컬 기본: SQLite 파일
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./nurs-local.db")
# pool_pre_ping: DB 재시작 후 끊긴 연결을 재사용하지 않는다
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def now() -> datetime:
    """UTC 기준 naive datetime. SQLite·PostgreSQL에서 같은 방식으로 비교되도록 시간대 정보 없이 저장한다."""
    return datetime.now(UTC).replace(tzinfo=None)
