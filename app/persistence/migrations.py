"""10/26 전환: 기존 데이터 보존. 빈 DB/기존 SQLite·PostgreSQL에서 재실행 가능."""
from sqlalchemy import inspect, text


def upgrade(engine):
    with engine.begin() as connection:
        if "nurses" in inspect(connection).get_table_names():
            columns = {c["name"] for c in inspect(connection).get_columns("nurses")}
            if "is_preceptor" not in columns:
                connection.execute(text("ALTER TABLE nurses ADD COLUMN is_preceptor BOOLEAN NOT NULL DEFAULT FALSE"))
            if "staff_type" not in columns:
                connection.execute(text("ALTER TABLE nurses ADD COLUMN staff_type VARCHAR(20) NOT NULL DEFAULT 'NURSE'"))
            connection.execute(text("UPDATE nurses SET is_preceptor = TRUE, duty_role = 'GENERAL' WHERE duty_role = 'PRECEPTOR'"))
