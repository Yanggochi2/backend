#!/usr/bin/env python3
"""프론트엔드 체험용 데모 병동, 간호사, 이번 달 근무표를 준비한다."""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ward_api.db import Database
from ward_api.errors import ApiError
from ward_api.features import FeatureWardService


def main() -> None:
    db_path = os.getenv("WARD_DB_PATH", "./var/ward_scheduler.db")
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db = Database(db_path)
    db.initialize()
    seed = db.seed_demo()
    service = FeatureWardService(db)
    actor_id = seed["userId"]

    existing, _ = service.list_nurses(actor_id, {"includeRetired": "true", "size": "100"})
    existing_names = {item["name"] for item in existing}
    demo_nurses = [
        ("김차지", "CHARGE", 5, 96),
        ("이선임", "GENERAL", 4, 72),
        ("박간호", "GENERAL", 3, 48),
        ("최간호", "GENERAL", 3, 36),
        ("정간호", "GENERAL", 3, 24),
    ]
    for name, duty_role, skill_level, career_months in demo_nurses:
        if name in existing_names:
            continue
        service.create_nurse(actor_id, {
            "name": name,
            "dutyRole": duty_role,
            "status": "ACTIVE",
            "joinedAt": "2024-01-01",
            "careerMonths": career_months,
            "skillLevel": skill_level,
            "affiliationStart": "2024-01-01",
            "affiliationEnd": None,
            "preceptorOf": [],
        })

    # 데모 화면에서 수간호사 권한 섹션이 실제 역할 필터 결과를 보여 주도록 한다.
    with db.transaction() as conn:
        conn.execute(
            "UPDATE nurses SET role='HEAD_NURSE',updated_at=? WHERE ward_id=? AND name='김차지'",
            (date.today().isoformat(), seed["wardId"]),
        )

    year_month = date.today().strftime("%Y-%m")
    try:
        schedule = service.find_schedule(actor_id, year_month)
    except ApiError as error:
        if error.status != 404:
            raise
        schedule = service.create_schedule(actor_id, year_month)
    if not schedule["cells"]:
        service.generate_schedule(actor_id, schedule["id"], {"fixedCells": []})
        schedule = service.get_schedule(actor_id, schedule["id"])

    print(f"데모 사용자: {actor_id}")
    print(f"데모 병동: {seed['wardId']}")
    print(f"근무표: {schedule['yearMonth']} / {len(schedule['cells'])}개 셀")


if __name__ == "__main__":
    main()
