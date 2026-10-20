import os
from datetime import UTC, datetime

from sqlalchemy import URL, create_engine, make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# 우선순위: DATABASE_URL > POSTGRES_* (compose) > 로컬 SQLite 파일.
# POSTGRES_*는 URL.create로 조립해 비밀번호에 @ : / 같은 문자가 있어도 깨지지 않는다
DATABASE_URL = make_url(os.getenv("DATABASE_URL") or (
    URL.create("postgresql+psycopg", os.environ["POSTGRES_USER"], os.environ["POSTGRES_PASSWORD"],
               os.getenv("POSTGRES_HOST", "postgres"), 5432, os.environ["POSTGRES_DB"])
    if os.getenv("POSTGRES_PASSWORD") else "sqlite:///./nurs-local.db"))
# pool_pre_ping: DB 재시작 후 끊긴 연결을 재사용하지 않는다
engine = create_engine(DATABASE_URL, pool_pre_ping=True,
                       connect_args={"check_same_thread": False} if DATABASE_URL.get_backend_name() == "sqlite" else {})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def now() -> datetime:
    """UTC 기준 naive datetime. SQLite·PostgreSQL에서 같은 방식으로 비교되도록 시간대 정보 없이 저장한다."""
    return datetime.now(UTC).replace(tzinfo=None)
