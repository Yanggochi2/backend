"""RULE-02 기본 공휴일"""
import uuid
from datetime import date

from app.business.rules import in_month
from app.persistence.db import SessionLocal


def test_labor_and_constitution_day_from_2026():
    with SessionLocal() as db:
        names = lambda y, mo: {h["date"]: h["name"] for h in in_month(db, uuid.uuid4(), date(y, mo, 1))}  # noqa: E731
        assert names(2026, 5)["2026-05-01"] == "노동절" and names(2026, 7)["2026-07-17"] == "제헌절"
        assert "2025-05-01" not in names(2025, 5) and "2025-07-17" not in names(2025, 7)
