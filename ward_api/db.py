from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS wards (
    id TEXT PRIMARY KEY,
    hospital_name TEXT NOT NULL,
    ward_name TEXT NOT NULL,
    required_d INTEGER NOT NULL CHECK(required_d >= 0),
    required_e INTEGER NOT NULL CHECK(required_e >= 0),
    required_n INTEGER NOT NULL CHECK(required_n >= 0),
    created_at TEXT NOT NULL,
    join_code TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT,
    terms_agreed INTEGER NOT NULL DEFAULT 0,
    account_status TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    token_hash TEXT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS memberships (
    id TEXT,
    user_id TEXT NOT NULL REFERENCES users(id),
    ward_id TEXT NOT NULL REFERENCES wards(id),
    role TEXT NOT NULL CHECK(role IN ('HEAD_NURSE', 'NURSE')),
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    joined_at TEXT,
    PRIMARY KEY(user_id, ward_id)
);

CREATE TABLE IF NOT EXISTS nurses (
    id TEXT PRIMARY KEY,
    ward_id TEXT NOT NULL REFERENCES wards(id),
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('HEAD_NURSE', 'NURSE')),
    duty_role TEXT NOT NULL CHECK(duty_role IN ('CHARGE', 'PRECEPTOR', 'NEW', 'GENERAL')),
    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'PREGNANT', 'ON_LEAVE', 'RETIRED')),
    joined_at TEXT NOT NULL,
    career_months INTEGER NOT NULL CHECK(career_months >= 0),
    skill_level INTEGER NOT NULL CHECK(skill_level BETWEEN 1 AND 5),
    affiliation_start TEXT NOT NULL,
    affiliation_end TEXT,
    preceptor_of TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nurses_ward ON nurses(ward_id, status, name);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    ward_id TEXT NOT NULL REFERENCES wards(id),
    year_month TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('DRAFT', 'GENERATING', 'CONFIRMED', 'ARCHIVED')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    confirmed_at TEXT,
    UNIQUE(ward_id, year_month)
);

CREATE TABLE IF NOT EXISTS schedule_cells (
    schedule_id TEXT NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    nurse_id TEXT NOT NULL REFERENCES nurses(id),
    work_date TEXT NOT NULL,
    duty_code TEXT CHECK(duty_code IN ('D', 'E', 'N', 'O', 'AL', 'ED') OR duty_code IS NULL),
    PRIMARY KEY(schedule_id, nurse_id, work_date)
);

CREATE TABLE IF NOT EXISTS generation_jobs (
    id TEXT PRIMARY KEY,
    ward_id TEXT NOT NULL REFERENCES wards(id),
    schedule_id TEXT NOT NULL REFERENCES schedules(id),
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    hard_violation_count INTEGER NOT NULL DEFAULT 0,
    metrics TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    ward_id TEXT NOT NULL REFERENCES wards(id),
    actor_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS membership_requests (
    id TEXT PRIMARY KEY,
    ward_id TEXT NOT NULL REFERENCES wards(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED')),
    requested_at TEXT NOT NULL,
    processed_at TEXT,
    processor_id TEXT,
    rejection_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_membership_requests_ward ON membership_requests(ward_id, status, requested_at);

CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY,
    ward_id TEXT NOT NULL REFERENCES wards(id),
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('HARD','SOFT')),
    enabled INTEGER NOT NULL DEFAULT 1,
    parameters TEXT NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(ward_id, code)
);

CREATE TABLE IF NOT EXISTS holidays (
    ward_id TEXT NOT NULL REFERENCES wards(id),
    holiday_date TEXT NOT NULL,
    is_holiday INTEGER NOT NULL,
    name TEXT,
    PRIMARY KEY(ward_id, holiday_date)
);

CREATE TABLE IF NOT EXISTS off_targets (
    ward_id TEXT NOT NULL REFERENCES wards(id),
    year_month TEXT NOT NULL,
    target_count INTEGER NOT NULL,
    PRIMARY KEY(ward_id, year_month)
);

CREATE TABLE IF NOT EXISTS work_requests (
    id TEXT PRIMARY KEY,
    ward_id TEXT NOT NULL REFERENCES wards(id),
    applicant_id TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('ANNUAL_LEAVE','PREFERRED_OFF','PREFERRED_SHIFT')),
    target_dates TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    reason_detail TEXT,
    preferred_duty TEXT,
    status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED','CANCELLED')),
    processor_id TEXT,
    processed_at TEXT,
    rejection_reason TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_requests_ward ON work_requests(ward_id, status, created_at);

CREATE TABLE IF NOT EXISTS notification_settings (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    schedule_confirmed INTEGER NOT NULL DEFAULT 1,
    schedule_cancelled INTEGER NOT NULL DEFAULT 1,
    request_result INTEGER NOT NULL DEFAULT 1,
    duty_reminder INTEGER NOT NULL DEFAULT 1,
    web_push INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read, created_at);

CREATE TABLE IF NOT EXISTS schedule_locks (
    schedule_id TEXT PRIMARY KEY REFERENCES schedules(id) ON DELETE CASCADE,
    ward_id TEXT NOT NULL REFERENCES wards(id),
    holder_user_id TEXT NOT NULL REFERENCES users(id),
    lock_token TEXT NOT NULL UNIQUE,
    acquired_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS import_previews (
    id TEXT PRIMARY KEY,
    ward_id TEXT NOT NULL REFERENCES wards(id),
    schedule_id TEXT NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    filename TEXT NOT NULL,
    rows_json TEXT NOT NULL,
    nurse_mappings TEXT NOT NULL DEFAULT '{}',
    duty_mappings TEXT NOT NULL DEFAULT '{}',
    errors_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_import_previews_schedule ON import_previews(schedule_id, created_at);

CREATE TABLE IF NOT EXISTS idempotency_records (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    method TEXT NOT NULL,
    request_path TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PROCESSING','COMPLETED')),
    response_status INTEGER,
    response_json TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(actor_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_idempotency_created ON idempotency_records(created_at);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._memory_connection = None
        if self.path == ":memory:":
            self._memory_connection = self._connect()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def connection(self):
        if self._memory_connection is not None:
            yield self._memory_connection
            return
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            self._migrate_users(conn)
            self._migrate_core(conn)
            conn.commit()

    @staticmethod
    def _migrate_users(conn) -> None:
        """초기 MVP 데이터베이스를 현재 인증 스키마로 안전하게 올린다."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        additions = {
            "password_hash": "TEXT",
            "terms_agreed": "INTEGER NOT NULL DEFAULT 0",
            "account_status": "TEXT NOT NULL DEFAULT 'ACTIVE'",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")

    @staticmethod
    def _migrate_core(conn) -> None:
        additions = {
            "wards": {"join_code": "TEXT"},
            "memberships": {"id": "TEXT", "joined_at": "TEXT"},
            "schedules": {"confirmed_at": "TEXT"},
            "sessions": {"token_hash": "TEXT"},
            "schedule_locks": {"expires_at": "TEXT"},
        }
        for table, columns in additions.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        now = now_iso()
        for row in conn.execute("SELECT id FROM wards WHERE join_code IS NULL OR join_code='' ").fetchall():
            conn.execute("UPDATE wards SET join_code=? WHERE id=?", (uuid.uuid4().hex[:8].upper(), row["id"]))
        conn.execute("UPDATE memberships SET id=COALESCE(id, lower(hex(randomblob(16)))), joined_at=COALESCE(joined_at, ?)", (now,))

    def seed_demo(self) -> dict:
        """개발 실행에 필요한 병동과 인증 주체를 한 번만 생성한다."""
        with self.transaction() as conn:
            existing = conn.execute("SELECT id FROM wards LIMIT 1").fetchone()
            if existing:
                member = conn.execute(
                    "SELECT user_id FROM memberships WHERE ward_id=? AND role='HEAD_NURSE' LIMIT 1",
                    (existing["id"],),
                ).fetchone()
                return {"wardId": existing["id"], "userId": member["user_id"]}

            ward_id = new_id()
            user_id = new_id()
            now = now_iso()
            conn.execute(
                "INSERT INTO wards(id,hospital_name,ward_name,required_d,required_e,required_n,created_at,join_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ward_id, "데모병원", "간호병동", 2, 2, 1, now, uuid.uuid4().hex[:8].upper()),
            )
            conn.execute(
                """INSERT INTO users(id, name, email, password_hash, terms_agreed, account_status)
                   VALUES (?, ?, ?, NULL, 1, 'ACTIVE')""",
                (user_id, "데모 수간호사", "head@example.com"),
            )
            conn.execute(
                "INSERT INTO memberships(id,user_id,ward_id,role,status,joined_at) VALUES (?, ?, ?, 'HEAD_NURSE', 'ACTIVE', ?)",
                (new_id(), user_id, ward_id, now),
            )
            return {"wardId": ward_id, "userId": user_id}


def row_dict(row):
    if row is None:
        return None
    return dict(row)


def json_value(value, default):
    if value is None:
        return default
    return json.loads(value)
