#!/usr/bin/env python3
"""SQLite 온라인 백업을 만들고 오래된 백업을 정리한다."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    source = Path(os.getenv("WARD_DB_PATH", "./var/ward_scheduler.db")).resolve()
    destination_dir = Path(os.getenv("WARD_BACKUP_DIR", "./var/backups")).resolve()
    retention = max(int(os.getenv("WARD_BACKUP_RETENTION", "14")), 1)
    if not source.is_file():
        raise SystemExit(f"데이터베이스 파일을 찾을 수 없습니다: {source}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = destination_dir / f"ward_scheduler-{stamp}.db"
    with sqlite3.connect(source) as source_conn, sqlite3.connect(destination) as backup_conn:
        source_conn.backup(backup_conn)
        result = backup_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            destination.unlink(missing_ok=True)
            raise SystemExit(f"백업 무결성 검사 실패: {result}")
    backups = sorted(destination_dir.glob("ward_scheduler-*.db"), reverse=True)
    for old in backups[retention:]:
        old.unlink()
    print(destination)


if __name__ == "__main__":
    main()
