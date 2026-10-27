"""Alembic 도입 전까지 기존 설치의 작은 호환 마이그레이션을 적용한다."""
from sqlalchemy import inspect


def migrate_legacy_schema(engine) -> None:
    """create_all이 기존 테이블에 열을 추가하지 않으므로 이번 도메인 변경 열을 보완한다."""
    with engine.begin() as connection:
        inspector = inspect(connection)
        if "nurses" in inspector.get_table_names():
            columns = {column["name"] for column in inspector.get_columns("nurses")}
            if "rank" not in columns:
                connection.exec_driver_sql("ALTER TABLE nurses ADD COLUMN rank VARCHAR(20)")
            if "hire_year" not in columns:
                connection.exec_driver_sql("ALTER TABLE nurses ADD COLUMN hire_year INTEGER")
            if "is_preceptor" not in columns:
                connection.exec_driver_sql("ALTER TABLE nurses ADD COLUMN is_preceptor BOOLEAN DEFAULT FALSE")
            connection.exec_driver_sql(
                "UPDATE nurses SET rank = CASE WHEN role = 'HEAD_NURSE' THEN 'HEAD_NURSE' "
                "WHEN duty_role = 'CHARGE' THEN 'CHARGE_NURSE' ELSE 'STAFF_NURSE' END WHERE rank IS NULL"
            )
            if connection.dialect.name == "postgresql":
                connection.exec_driver_sql(
                    "UPDATE nurses SET hire_year = EXTRACT(YEAR FROM joined_at)::INTEGER WHERE hire_year IS NULL"
                )
            else:
                connection.exec_driver_sql(
                    "UPDATE nurses SET hire_year = CAST(strftime('%Y', joined_at) AS INTEGER) WHERE hire_year IS NULL"
                )
            connection.exec_driver_sql("UPDATE nurses SET is_preceptor = FALSE WHERE is_preceptor IS NULL")

        inspector = inspect(connection)
        if "schedules" in inspector.get_table_names():
            columns = {column["name"] for column in inspector.get_columns("schedules")}
            if "share_token_hash" not in columns:
                connection.exec_driver_sql("ALTER TABLE schedules ADD COLUMN share_token_hash VARCHAR(64)")
                connection.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_schedules_share_token_hash ON schedules (share_token_hash)"
                )
            if "shared_at" not in columns:
                connection.exec_driver_sql("ALTER TABLE schedules ADD COLUMN shared_at TIMESTAMP")
