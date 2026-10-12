from __future__ import annotations

import calendar
import json
import sqlite3
import re
import threading
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from .auth import expiry_iso, hash_password, is_expired, new_token, token_digest, verify_password
from .db import Database, json_value, new_id, now_iso, row_dict
from .errors import ApiError, require


NURSE_FIELDS = {
    "name", "dutyRole", "status", "joinedAt", "careerMonths", "skillLevel",
    "affiliationStart", "affiliationEnd", "preceptorOf",
}
VALID_DUTY_ROLES = {"CHARGE", "PRECEPTOR", "NEW", "GENERAL"}
VALID_STATUSES = {"ACTIVE", "PREGNANT", "ON_LEAVE", "RETIRED"}
VALID_DUTIES = {"D", "E", "N", "O", "AL", "ED", None}


def parse_date(value: str, field: str = "date") -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ApiError(400, "VALIDATION_ERROR", f"{field}는 YYYY-MM-DD 형식이어야 합니다.")


def parse_year_month(value: str) -> tuple[int, int]:
    try:
        parsed = datetime.strptime(value, "%Y-%m")
    except (TypeError, ValueError):
        raise ApiError(400, "INVALID_YEAR_MONTH", "yearMonth는 YYYY-MM 형식이어야 합니다.")
    return parsed.year, parsed.month


def nurse_from_row(row, include_sensitive=True):
    item = {
        "id": row["id"],
        "name": row["name"],
        "role": row["role"],
        "dutyRole": row["duty_role"],
        "affiliationStart": row["affiliation_start"],
        "affiliationEnd": row["affiliation_end"],
        "version": row["version"],
    }
    if include_sensitive:
        item.update({
            "status": row["status"],
            "joinedAt": row["joined_at"],
            "careerMonths": row["career_months"],
            "skillLevel": row["skill_level"],
            "preceptorOf": json_value(row["preceptor_of"], []),
        })
    return item


class WardService:
    def __init__(self, db: Database, async_generation: bool = False):
        self.db = db
        self.async_generation = async_generation

    def signup(self, body: dict):
        name = body.get("name")
        email = body.get("email")
        password = body.get("password")
        require(isinstance(name, str) and 1 <= len(name.strip()) <= 50,
                400, "VALIDATION_ERROR", "name은 1자부터 50자여야 합니다.")
        require(isinstance(email, str) and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email),
                400, "VALIDATION_ERROR", "email 형식이 올바르지 않습니다.")
        require(isinstance(password, str) and len(password) >= 8 and re.search(r"[A-Za-z]", password) and re.search(r"\d", password),
                400, "VALIDATION_ERROR", "password는 8자 이상이며 영문과 숫자를 포함해야 합니다.")
        require(body.get("termsAgreed") is True, 400, "TERMS_NOT_AGREED", "약관 동의가 필요합니다.")
        user_id = new_id()
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    """INSERT INTO users(id, name, email, password_hash, terms_agreed, account_status)
                       VALUES (?, ?, ?, ?, 1, 'ACTIVE')""",
                    (user_id, name.strip(), email.strip().lower(), hash_password(password)),
                )
        except sqlite3.IntegrityError:
            raise ApiError(409, "EMAIL_ALREADY_EXISTS", "이미 가입된 이메일입니다.")
        return {"id": user_id, "name": name.strip(), "email": email.strip().lower(), "accountStatus": "ACTIVE"}

    def login(self, body: dict):
        email = body.get("email")
        password = body.get("password")
        require(isinstance(email, str) and isinstance(password, str),
                400, "VALIDATION_ERROR", "email과 password가 필요합니다.")
        with self.db.transaction() as conn:
            user = conn.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
            require(user is not None and verify_password(password, user["password_hash"]),
                    401, "INVALID_CREDENTIALS", "이메일 또는 비밀번호가 올바르지 않습니다.")
            require(user["account_status"] == "ACTIVE", 423, "ACCOUNT_DISABLED", "비활성 계정입니다.")
            session_id = new_token()
            csrf_token = new_token()
            conn.execute(
                """INSERT INTO sessions(id,token_hash,user_id,csrf_token,expires_at,created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (new_id(), token_digest(session_id), user["id"], csrf_token, expiry_iso(), now_iso()),
            )
        return {
            "id": user["id"], "name": user["name"], "email": user["email"],
            "accountStatus": user["account_status"],
            "sessionId": session_id,
            "csrfToken": csrf_token,
        }

    def refresh_session(self, session_id: str):
        session = self.session_user(session_id)
        with self.db.transaction() as conn:
            conn.execute("UPDATE sessions SET expires_at=? WHERE token_hash=? OR id=?",
                         (expiry_iso(), token_digest(session_id), session_id))
        return session

    def session_user(self, session_id: str):
        with self.db.transaction() as conn:
            row = conn.execute(
                """SELECT s.*, u.account_status FROM sessions s JOIN users u ON u.id=s.user_id
                   WHERE s.token_hash=? OR s.id=?""", (token_digest(session_id), session_id)
            ).fetchone()
            require(row is not None and not is_expired(row["expires_at"]),
                    401, "UNAUTHENTICATED", "세션이 없거나 만료되었습니다.")
            require(row["account_status"] == "ACTIVE", 423, "ACCOUNT_DISABLED", "비활성 계정입니다.")
        return {"userId": row["user_id"], "csrfToken": row["csrf_token"]}

    def logout(self, session_id: str):
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=? OR id=?", (token_digest(session_id), session_id))

    def get_me(self, actor_id: str):
        with self.db.connection() as conn:
            user = conn.execute("SELECT * FROM users WHERE id=?", (actor_id,)).fetchone()
            require(user is not None, 401, "UNAUTHENTICATED", "사용자를 찾을 수 없습니다.")
            membership = conn.execute(
                "SELECT * FROM memberships WHERE user_id=? AND status='ACTIVE' LIMIT 1", (actor_id,)
            ).fetchone()
            ward = None
            if membership:
                ward_row = conn.execute("SELECT * FROM wards WHERE id=?", (membership["ward_id"],)).fetchone()
                ward = {
                    "id": ward_row["id"], "hospitalName": ward_row["hospital_name"],
                    "wardName": ward_row["ward_name"],
                    "requiredStaff": {"D": ward_row["required_d"], "E": ward_row["required_e"], "N": ward_row["required_n"]},
                    "createdAt": ward_row["created_at"],
                }
        return {
            "user": {"id": user["id"], "name": user["name"], "email": user["email"], "accountStatus": user["account_status"]},
            "membership": ({"id": membership["id"], "wardId": membership["ward_id"], "userId": membership["user_id"],
                            "role": membership["role"], "status": membership["status"], "joinedAt": membership["joined_at"]}
                           if membership else None),
            "ward": ward,
        }

    def create_ward(self, actor_id: str, body: dict):
        hospital_name = body.get("hospitalName")
        ward_name = body.get("wardName")
        required_staff = body.get("requiredStaff")
        require(isinstance(hospital_name, str) and hospital_name.strip(),
                400, "VALIDATION_ERROR", "hospitalName이 필요합니다.")
        require(isinstance(ward_name, str) and ward_name.strip(),
                400, "VALIDATION_ERROR", "wardName이 필요합니다.")
        require(isinstance(required_staff, dict) and set(required_staff) >= {"D", "E", "N"},
                400, "VALIDATION_ERROR", "requiredStaff의 D, E, N이 필요합니다.")
        for duty in ("D", "E", "N"):
            require(isinstance(required_staff[duty], int) and required_staff[duty] >= 0,
                    422, "INVALID_STAFFING", f"requiredStaff.{duty}는 0 이상 정수여야 합니다.")
        ward_id = new_id()
        with self.db.transaction() as conn:
            user = conn.execute("SELECT * FROM users WHERE id=?", (actor_id,)).fetchone()
            require(user is not None, 401, "UNAUTHENTICATED", "사용자를 찾을 수 없습니다.")
            membership = conn.execute(
                "SELECT 1 FROM memberships WHERE user_id=? AND status='ACTIVE'", (actor_id,)
            ).fetchone()
            require(membership is None, 409, "MEMBERSHIP_ALREADY_EXISTS", "이미 소속된 병동이 있습니다.")
            now = now_iso()
            conn.execute(
                """INSERT INTO wards(id,hospital_name,ward_name,required_d,required_e,required_n,created_at,join_code)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (ward_id, hospital_name.strip(), ward_name.strip(), required_staff["D"],
                 required_staff["E"], required_staff["N"], now, new_id().replace("-", "")[:8].upper()),
            )
            conn.execute(
                """INSERT INTO memberships(id,user_id,ward_id,role,status,joined_at)
                   VALUES (?, ?, ?, 'HEAD_NURSE', 'ACTIVE', ?)""",
                (new_id(), actor_id, ward_id, now),
            )
            actor = {"ward_id": ward_id, "user_id": actor_id}
            result = {
                "id": ward_id, "hospitalName": hospital_name.strip(), "wardName": ward_name.strip(),
                "requiredStaff": {d: required_staff[d] for d in ("D", "E", "N")}, "createdAt": now,
            }
            self._audit(conn, actor, "WARD_CREATED", "WARD", ward_id, None, result)
            membership = conn.execute("SELECT * FROM memberships WHERE user_id=? AND ward_id=?", (actor_id, ward_id)).fetchone()
            join_code = conn.execute("SELECT join_code FROM wards WHERE id=?", (ward_id,)).fetchone()["join_code"]
        return {**result, "ward": result, "membership": self._membership(membership), "joinCode": join_code}

    def actor_context(self, actor_id: str):
        with self.db.connection() as conn:
            row = conn.execute(
                """SELECT m.user_id, m.ward_id, m.role, w.hospital_name, w.ward_name,
                          w.required_d, w.required_e, w.required_n
                   FROM memberships m JOIN wards w ON w.id=m.ward_id
                   WHERE m.user_id=? AND m.status='ACTIVE'""",
                (actor_id,),
            ).fetchone()
        require(row is not None, 401, "UNAUTHENTICATED", "활성 병동 소속을 찾을 수 없습니다.")
        return dict(row)

    @staticmethod
    def require_head(actor):
        require(actor["role"] == "HEAD_NURSE", 403, "FORBIDDEN", "수간호사 권한이 필요합니다.")

    def get_ward(self, actor_id: str):
        actor = self.actor_context(actor_id)
        with self.db.connection() as conn:
            created_at = conn.execute("SELECT created_at FROM wards WHERE id=?", (actor["ward_id"],)).fetchone()["created_at"]
        return {
            "id": actor["ward_id"],
            "hospitalName": actor["hospital_name"],
            "wardName": actor["ward_name"],
            "requiredStaff": {
                "D": actor["required_d"],
                "E": actor["required_e"],
                "N": actor["required_n"],
            },
            "createdAt": created_at,
        }

    def create_nurse(self, actor_id: str, body: dict):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        self._validate_nurse(body, partial=False)
        nurse_id = new_id()
        now = now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO nurses(
                    id, ward_id, name, role, duty_role, status, joined_at,
                    career_months, skill_level, affiliation_start, affiliation_end,
                    preceptor_of, version, created_at, updated_at
                ) VALUES (?, ?, ?, 'NURSE', ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    nurse_id, actor["ward_id"], body["name"].strip(), body["dutyRole"],
                    body["status"], body["joinedAt"], body["careerMonths"],
                    body["skillLevel"], body["affiliationStart"], body.get("affiliationEnd"),
                    json.dumps(body.get("preceptorOf", [])), now, now,
                ),
            )
            row = conn.execute("SELECT * FROM nurses WHERE id=?", (nurse_id,)).fetchone()
            self._audit(conn, actor, "NURSE_CREATED", "NURSE", nurse_id, None, nurse_from_row(row))
        return nurse_from_row(row)

    def list_nurses(self, actor_id: str, params: dict):
        actor = self.actor_context(actor_id)
        where = ["ward_id=?"]
        values = [actor["ward_id"]]
        if params.get("includeRetired", "false").lower() != "true":
            where.append("status<>'RETIRED'")
        for query_name, column in (("status", "status"), ("dutyRole", "duty_role"), ("role", "role")):
            if params.get(query_name):
                where.append(f"{column}=?")
                values.append(params[query_name])
        if params.get("q"):
            where.append("name LIKE ?")
            values.append(f"%{params['q']}%")
        page = max(int(params.get("page", 0)), 0)
        size = min(max(int(params.get("size", 20)), 1), 100)
        sql_where = " AND ".join(where)
        with self.db.connection() as conn:
            total = conn.execute(f"SELECT COUNT(*) n FROM nurses WHERE {sql_where}", values).fetchone()["n"]
            rows = conn.execute(
                f"SELECT * FROM nurses WHERE {sql_where} ORDER BY name LIMIT ? OFFSET ?",
                (*values, size, page * size),
            ).fetchall()
        sensitive = actor["role"] == "HEAD_NURSE"
        return [nurse_from_row(r, sensitive) for r in rows], {
            "page": page, "size": size, "totalElements": total,
            "totalPages": (total + size - 1) // size,
        }

    def get_nurse(self, actor_id: str, nurse_id: str):
        actor = self.actor_context(actor_id)
        row = self._nurse_row(actor["ward_id"], nurse_id)
        return nurse_from_row(row, actor["role"] == "HEAD_NURSE")

    def update_nurse(self, actor_id: str, nurse_id: str, body: dict):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        require("role" not in body, 400, "ROLE_NOT_EDITABLE", "role은 권한 이관 API로만 변경할 수 있습니다.")
        require(bool(body), 400, "VALIDATION_ERROR", "변경할 필드가 없습니다.")
        unknown = set(body) - NURSE_FIELDS - {"version"}
        require(not unknown, 400, "VALIDATION_ERROR", f"지원하지 않는 필드입니다: {sorted(unknown)}")
        self._validate_nurse(body, partial=True)
        with self.db.transaction() as conn:
            before_row = conn.execute(
                "SELECT * FROM nurses WHERE id=? AND ward_id=?", (nurse_id, actor["ward_id"])
            ).fetchone()
            require(before_row is not None, 404, "NURSE_NOT_FOUND", "간호사를 찾을 수 없습니다.")
            if "version" in body:
                require(body["version"] == before_row["version"], 409, "VERSION_CONFLICT", "간호사 정보가 변경되었습니다.")
            merged = dict(before_row)
            mapping = {
                "name": "name", "dutyRole": "duty_role", "status": "status",
                "joinedAt": "joined_at", "careerMonths": "career_months",
                "skillLevel": "skill_level", "affiliationStart": "affiliation_start",
                "affiliationEnd": "affiliation_end", "preceptorOf": "preceptor_of",
            }
            assignments, values = [], []
            for key, column in mapping.items():
                if key in body:
                    assignments.append(f"{column}=?")
                    value = json.dumps(body[key]) if key == "preceptorOf" else body[key]
                    values.append(value)
                    merged[column] = value
            self._validate_affiliation(merged["affiliation_start"], merged.get("affiliation_end"))
            assignments.extend(["version=version+1", "updated_at=?"])
            values.extend([now_iso(), nurse_id, actor["ward_id"]])
            conn.execute(
                f"UPDATE nurses SET {', '.join(assignments)} WHERE id=? AND ward_id=?", values
            )
            after_row = conn.execute("SELECT * FROM nurses WHERE id=?", (nurse_id,)).fetchone()
            self._audit(conn, actor, "NURSE_UPDATED", "NURSE", nurse_id,
                        nurse_from_row(before_row), nurse_from_row(after_row))
        violations = self._violations_for_nurse(actor["ward_id"], nurse_id)
        return {**nurse_from_row(after_row), "violations": violations}

    def retire_nurse(self, actor_id: str, nurse_id: str, affiliation_end: str):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        parse_date(affiliation_end, "affiliationEnd")
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM nurses WHERE id=? AND ward_id=?", (nurse_id, actor["ward_id"])
            ).fetchone()
            require(row is not None, 404, "NURSE_NOT_FOUND", "간호사를 찾을 수 없습니다.")
            require(row["role"] != "HEAD_NURSE", 409, "LAST_HEAD_NURSE", "수간호사 권한을 먼저 이관해야 합니다.")
            self._validate_affiliation(row["affiliation_start"], affiliation_end)
            conn.execute(
                "UPDATE nurses SET status='RETIRED', affiliation_end=?, version=version+1, updated_at=? WHERE id=?",
                (affiliation_end, now_iso(), nurse_id),
            )
            after = conn.execute("SELECT * FROM nurses WHERE id=?", (nurse_id,)).fetchone()
            self._audit(conn, actor, "NURSE_RETIRED", "NURSE", nurse_id,
                        nurse_from_row(row), nurse_from_row(after))
        return {**nurse_from_row(after), "violations": self._violations_for_nurse(actor["ward_id"], nurse_id)}

    def create_schedule(self, actor_id: str, year_month: str):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        year, month = parse_year_month(year_month)
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
        with self.db.transaction() as conn:
            count = conn.execute(
                """SELECT COUNT(*) n FROM nurses WHERE ward_id=? AND status<>'RETIRED'
                   AND affiliation_start<=? AND (affiliation_end IS NULL OR affiliation_end>=?)""",
                (actor["ward_id"], end.isoformat(), start.isoformat()),
            ).fetchone()["n"]
            require(count > 0, 422, "NO_ACTIVE_NURSES", "대상 월에 소속된 간호사가 없습니다.")
            schedule_id = new_id()
            now = now_iso()
            try:
                conn.execute(
                    """INSERT INTO schedules(id,ward_id,year_month,status,version,created_at,updated_at,confirmed_at)
                       VALUES (?, ?, ?, 'DRAFT', 1, ?, ?, NULL)""",
                    (schedule_id, actor["ward_id"], year_month, now, now),
                )
            except sqlite3.IntegrityError:
                raise ApiError(409, "SCHEDULE_ALREADY_EXISTS", "동일 연월의 근무표가 이미 있습니다.")
            self._audit(conn, actor, "SCHEDULE_CREATED", "SCHEDULE", schedule_id, None,
                        {"yearMonth": year_month, "status": "DRAFT"})
        return self.get_schedule(actor_id, schedule_id)

    def find_schedule(self, actor_id: str, year_month: str):
        actor = self.actor_context(actor_id)
        parse_year_month(year_month)
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT id FROM schedules WHERE ward_id=? AND year_month=?",
                (actor["ward_id"], year_month),
            ).fetchone()
        require(row is not None, 404, "SCHEDULE_NOT_FOUND", "근무표를 찾을 수 없습니다.")
        return self.get_schedule(actor_id, row["id"])

    def get_schedule(self, actor_id: str, schedule_id: str):
        actor = self.actor_context(actor_id)
        schedule = self._schedule_row(actor["ward_id"], schedule_id)
        if actor["role"] != "HEAD_NURSE":
            require(schedule["status"] in ("CONFIRMED", "ARCHIVED"), 404, "SCHEDULE_NOT_FOUND", "근무표를 찾을 수 없습니다.")
        nurses = self._schedule_nurses(actor["ward_id"], schedule["year_month"])
        with self.db.connection() as conn:
            cells = conn.execute(
                "SELECT nurse_id, work_date, duty_code FROM schedule_cells WHERE schedule_id=? ORDER BY work_date, nurse_id",
                (schedule_id,),
            ).fetchall()
        result = {
            "id": schedule["id"], "yearMonth": schedule["year_month"],
            "status": schedule["status"], "version": schedule["version"],
            "nurses": [nurse_from_row(n, actor["role"] == "HEAD_NURSE") for n in nurses],
            "cells": [{"nurseId": c["nurse_id"], "date": c["work_date"], "dutyCode": c["duty_code"],
                       "editable": schedule["status"] == "DRAFT"} for c in cells],
            "coverage": self.coverage(actor_id, schedule_id),
            "confirmedAt": schedule["confirmed_at"],
        }
        if actor["role"] == "HEAD_NURSE":
            result["violations"] = self.violations(actor_id, schedule_id)
        return result

    def patch_cells(self, actor_id: str, schedule_id: str, body: dict, lock_token: str | None = None):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        require(isinstance(body.get("baseVersion"), int), 400, "VALIDATION_ERROR", "baseVersion이 필요합니다.")
        changes = body.get("changes")
        require(isinstance(changes, list) and changes, 400, "VALIDATION_ERROR", "changes는 비어 있을 수 없습니다.")
        with self.db.transaction() as conn:
            conn.execute(
                "DELETE FROM schedule_locks WHERE schedule_id=? AND expires_at IS NOT NULL AND expires_at<=?",
                (schedule_id, now_iso()),
            )
            held_lock = conn.execute(
                "SELECT * FROM schedule_locks WHERE schedule_id=? AND ward_id=?",
                (schedule_id, actor["ward_id"]),
            ).fetchone()
            if held_lock:
                require(lock_token == held_lock["lock_token"] and actor_id == held_lock["holder_user_id"],
                        423, "SCHEDULE_LOCKED", "다른 사용자가 근무표를 편집 중입니다.")
            schedule = conn.execute(
                "SELECT * FROM schedules WHERE id=? AND ward_id=?", (schedule_id, actor["ward_id"])
            ).fetchone()
            require(schedule is not None, 404, "SCHEDULE_NOT_FOUND", "근무표를 찾을 수 없습니다.")
            require(schedule["status"] == "DRAFT", 409, "INVALID_SCHEDULE_STATE", "DRAFT 근무표만 수정할 수 있습니다.")
            require(schedule["version"] == body["baseVersion"], 409, "VERSION_CONFLICT", "근무표가 변경되었습니다.")
            for change in changes:
                self._validate_cell_change(conn, actor["ward_id"], schedule, change)
            for change in changes:
                conn.execute(
                    """INSERT INTO schedule_cells(schedule_id, nurse_id, work_date, duty_code)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(schedule_id, nurse_id, work_date)
                       DO UPDATE SET duty_code=excluded.duty_code""",
                    (schedule_id, change["nurseId"], change["date"], change.get("dutyCode")),
                )
            conn.execute(
                "UPDATE schedules SET version=version+1, updated_at=? WHERE id=?",
                (now_iso(), schedule_id),
            )
            new_version = schedule["version"] + 1
            self._audit(conn, actor, "SCHEDULE_CELLS_UPDATED", "SCHEDULE", schedule_id,
                        {"version": schedule["version"]}, {"version": new_version, "changedCells": len(changes)})
        return {
            "version": new_version,
            "changedCells": [{**change, "editable": True} for change in changes],
            "coverage": self.coverage(actor_id, schedule_id),
            "violations": self.violations(actor_id, schedule_id),
        }

    def coverage(self, actor_id: str, schedule_id: str):
        actor = self.actor_context(actor_id)
        schedule = self._schedule_row(actor["ward_id"], schedule_id)
        year, month = parse_year_month(schedule["year_month"])
        days = calendar.monthrange(year, month)[1]
        with self.db.connection() as conn:
            rows = conn.execute(
                """SELECT work_date, duty_code, COUNT(*) count FROM schedule_cells
                   WHERE schedule_id=? AND duty_code IN ('D','E','N')
                   GROUP BY work_date, duty_code""", (schedule_id,)
            ).fetchall()
        counts = {(r["work_date"], r["duty_code"]): r["count"] for r in rows}
        required = {"D": actor["required_d"], "E": actor["required_e"], "N": actor["required_n"]}
        result = []
        for day in range(1, days + 1):
            day_text = date(year, month, day).isoformat()
            for duty in ("D", "E", "N"):
                actual = counts.get((day_text, duty), 0)
                target = required[duty]
                status = "UNDER" if actual < target else "OVER" if actual > target else "MET"
                result.append({"date": day_text, "dutyCode": duty, "actualCount": actual,
                               "requiredCount": target, "status": status})
        return result

    def violations(self, actor_id: str, schedule_id: str):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        schedule = self._schedule_row(actor["ward_id"], schedule_id)
        nurses = {n["id"]: n for n in self._schedule_nurses(actor["ward_id"], schedule["year_month"])}
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT nurse_id, work_date, duty_code FROM schedule_cells WHERE schedule_id=?",
                (schedule_id,),
            ).fetchall()
        cells = defaultdict(dict)
        for row in rows:
            cells[row["work_date"]][row["nurse_id"]] = row["duty_code"]
        violations = []
        for work_date, assignments in cells.items():
            night_ids = [nid for nid, duty in assignments.items() if duty == "N"]
            has_experienced_night = any(nurses.get(nid) and nurses[nid]["duty_role"] != "NEW" for nid in night_ids)
            for nurse_id, duty in assignments.items():
                nurse = nurses.get(nurse_id)
                if not nurse or duty is None:
                    continue
                if nurse["duty_role"] == "CHARGE" and duty in ("E", "N"):
                    violations.append(self._violation("CHARGE_DAY_ONLY", nurse_id, work_date, duty, "차지는 E 또는 N에 배정할 수 없습니다."))
                if nurse["status"] == "PREGNANT" and duty == "N":
                    violations.append(self._violation("PREGNANT_NO_NIGHT", nurse_id, work_date, duty, "임신 상태 간호사는 N에 배정할 수 없습니다."))
                if nurse["duty_role"] == "NEW" and duty == "N" and not has_experienced_night:
                    violations.append(self._violation("NEW_NIGHT_SUPPORT", nurse_id, work_date, duty, "신입 N 배정에는 비신입 간호사가 함께 있어야 합니다."))
                work_day = parse_date(work_date)
                if not self._active_on(nurse, work_day):
                    violations.append(self._violation("OUTSIDE_AFFILIATION", nurse_id, work_date, duty, "소속 기간 밖에는 배정할 수 없습니다."))
        for nurse_id in nurses:
            dates = sorted(d for d, items in cells.items() if items.get(nurse_id) == "N")
            streak = 0
            previous = None
            for text in dates:
                current = parse_date(text)
                streak = streak + 1 if previous and current == previous + timedelta(days=1) else 1
                if streak > 3:
                    violations.append(self._violation("MAX_CONSECUTIVE_NIGHTS", nurse_id, text, "N", "연속 N은 3일을 초과할 수 없습니다."))
                previous = current
        for item in self.coverage(actor_id, schedule_id):
            if item["status"] == "UNDER":
                violations.append({
                    "id": f"coverage:{item['date']}:{item['dutyCode']}", "severity": "HARD",
                    "ruleId": "MIN_COVERAGE", "nurseId": None, "date": item["date"],
                    "currentValue": item["actualCount"],
                    "message": f"{item['dutyCode']} 필요 인원 {item['requiredCount']}명 중 {item['actualCount']}명만 배정되었습니다.",
                })
        return violations

    def generate_schedule(self, actor_id: str, schedule_id: str, body: dict):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        fixed = {(c["nurseId"], c["date"]) for c in body.get("fixedCells", [])}
        job_id = new_id()
        with self.db.transaction() as conn:
            schedule = conn.execute(
                "SELECT * FROM schedules WHERE id=? AND ward_id=?", (schedule_id, actor["ward_id"])
            ).fetchone()
            require(schedule is not None, 404, "SCHEDULE_NOT_FOUND", "근무표를 찾을 수 없습니다.")
            require(schedule["status"] == "DRAFT", 409, "INVALID_SCHEDULE_STATE", "DRAFT 근무표만 자동 편성할 수 있습니다.")
            now = now_iso()
            initial_status = "QUEUED" if self.async_generation else "RUNNING"
            initial_stage = "QUEUED" if self.async_generation else "OPTIMIZING"
            conn.execute(
                """INSERT INTO generation_jobs(id,ward_id,schedule_id,status,stage,hard_violation_count,
                   metrics,error,created_at,finished_at) VALUES (?,?,?,?,?,0,'{}',NULL,?,NULL)""",
                (job_id, actor["ward_id"], schedule_id, initial_status, initial_stage, now),
            )
            conn.execute("UPDATE schedules SET status='GENERATING', updated_at=? WHERE id=?", (now, schedule_id))
        if self.async_generation:
            threading.Thread(
                target=self._execute_generation,
                args=(actor_id, actor, schedule_id, fixed, job_id),
                name=f"generation-{job_id[:8]}",
                daemon=True,
            ).start()
            return self.get_generation(actor_id, job_id)
        self._execute_generation(actor_id, actor, schedule_id, fixed, job_id, raise_error=True)
        return self.get_generation(actor_id, job_id)

    def _execute_generation(self, actor_id, actor, schedule_id, fixed, job_id, raise_error=False):
        try:
            with self.db.transaction() as conn:
                conn.execute("UPDATE generation_jobs SET status='RUNNING',stage='OPTIMIZING' WHERE id=?", (job_id,))
            metrics = self._run_greedy_generation(actor, schedule_id, fixed)
            violations = self.violations(actor_id, schedule_id)
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE schedules SET status='DRAFT', version=version+1, updated_at=? WHERE id=?",
                    (now_iso(), schedule_id),
                )
                conn.execute(
                    """UPDATE generation_jobs SET status='SUCCEEDED', stage='COMPLETED',
                       hard_violation_count=?, metrics=?, finished_at=? WHERE id=?""",
                    (len([v for v in violations if v["severity"] == "HARD"]), json.dumps(metrics), now_iso(), job_id),
                )
                self._audit(conn, actor, "SCHEDULE_GENERATED", "SCHEDULE", schedule_id, None,
                            {"jobId": job_id, "metrics": metrics})
        except Exception as exc:
            with self.db.transaction() as conn:
                conn.execute("UPDATE schedules SET status='DRAFT', updated_at=? WHERE id=?", (now_iso(), schedule_id))
                conn.execute(
                    "UPDATE generation_jobs SET status='FAILED', stage='FAILED', error=?, finished_at=? WHERE id=?",
                    (str(exc), now_iso(), job_id),
                )
            if raise_error:
                raise

    def get_generation(self, actor_id: str, job_id: str):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM generation_jobs WHERE id=? AND ward_id=?", (job_id, actor["ward_id"])
            ).fetchone()
        require(row is not None, 404, "JOB_NOT_FOUND", "자동 생성 작업을 찾을 수 없습니다.")
        return {
            "id": row["id"], "scheduleId": row["schedule_id"], "status": row["status"],
            "stage": row["stage"], "hardViolationCount": row["hard_violation_count"],
            "metrics": json_value(row["metrics"], {}), "error": row["error"],
            "createdAt": row["created_at"], "finishedAt": row["finished_at"],
            "elapsedSeconds": 0, "progress": 100 if row["status"] == "SUCCEEDED" else 0,
            "conflicts": [], "relaxations": [],
        }

    @staticmethod
    def _membership(row):
        return {"id": row["id"], "wardId": row["ward_id"], "userId": row["user_id"],
                "role": row["role"], "status": row["status"], "joinedAt": row["joined_at"]}

    def _run_greedy_generation(self, actor, schedule_id: str, fixed: set):
        schedule = self._schedule_row(actor["ward_id"], schedule_id)
        year, month = parse_year_month(schedule["year_month"])
        nurses = self._schedule_nurses(actor["ward_id"], schedule["year_month"])
        required = {"D": actor["required_d"], "E": actor["required_e"], "N": actor["required_n"]}
        counts = {n["id"]: Counter() for n in nurses}
        assignments = {}
        nurse_ids = {n["id"] for n in nurses}
        annual_leave = set()
        preferred_off = set()
        preferred_shift = {}
        with self.db.connection() as conn:
            existing = conn.execute(
                "SELECT nurse_id, work_date, duty_code FROM schedule_cells WHERE schedule_id=?", (schedule_id,)
            ).fetchall()
            requests = conn.execute(
                """SELECT applicant_id,type,target_dates,preferred_duty FROM work_requests
                   WHERE ward_id=? AND status='APPROVED' AND target_dates LIKE ?""",
                (actor["ward_id"], f'%"{schedule["year_month"]}-%'),
            ).fetchall()
        for cell in existing:
            key = (cell["nurse_id"], cell["work_date"])
            if cell["duty_code"] == "AL" or key in fixed:
                assignments[key] = cell["duty_code"]
                if cell["duty_code"] in ("D", "E", "N"):
                    counts[cell["nurse_id"]][cell["duty_code"]] += 1
        for request in requests:
            if request["applicant_id"] not in nurse_ids:
                continue
            for target_date in json_value(request["target_dates"], []):
                if not target_date.startswith(f'{schedule["year_month"]}-'):
                    continue
                key = (request["applicant_id"], target_date)
                if request["type"] == "ANNUAL_LEAVE":
                    annual_leave.add(key)
                    assignments[key] = "AL"
                elif request["type"] == "PREFERRED_OFF":
                    preferred_off.add(key)
                elif request["type"] == "PREFERRED_SHIFT" and request["preferred_duty"]:
                    preferred_shift[key] = request["preferred_duty"]

        for day_num in range(1, calendar.monthrange(year, month)[1] + 1):
            work_day = date(year, month, day_num)
            day_text = work_day.isoformat()
            active = [n for n in nurses if self._active_on(n, work_day) and n["status"] != "ON_LEAVE"]
            assigned_ids = {nid for (nid, d), duty in assignments.items() if d == day_text and duty is not None}
            for duty in ("N", "E", "D"):
                already = sum(1 for (nid, d), value in assignments.items() if d == day_text and value == duty)
                needed = max(required[duty] - already, 0)
                candidates = [n for n in active if n["id"] not in assigned_ids and self._eligible(n, duty)]
                candidates.sort(key=lambda n: (
                    2 if (n["id"], day_text) in preferred_off else
                    0 if preferred_shift.get((n["id"], day_text)) == duty else
                    2 if preferred_shift.get((n["id"], day_text)) not in (None, duty) else 1,
                    1 if duty == "N" and n["duty_role"] == "NEW" else 0,
                    0 if duty == "D" and n["duty_role"] == "CHARGE" else 1,
                    counts[n["id"]][duty], sum(counts[n["id"]].values()), n["name"],
                ))
                selected = []
                for nurse in candidates:
                    if len(selected) >= needed:
                        break
                    if duty == "N" and self._night_streak(assignments, nurse["id"], work_day) >= 3:
                        continue
                    selected.append(nurse)
                for nurse in selected:
                    assignments[(nurse["id"], day_text)] = duty
                    assigned_ids.add(nurse["id"])
                    counts[nurse["id"]][duty] += 1
            for nurse in active:
                key = (nurse["id"], day_text)
                assignments.setdefault(key, "O")

        with self.db.transaction() as conn:
            for (nurse_id, work_date), duty in assignments.items():
                conn.execute(
                    """INSERT INTO schedule_cells VALUES (?, ?, ?, ?)
                       ON CONFLICT(schedule_id, nurse_id, work_date)
                       DO UPDATE SET duty_code=excluded.duty_code""",
                    (schedule_id, nurse_id, work_date, duty),
                )
        return {
            "assignedCells": len(assignments),
            "shiftCounts": {nid: dict(counter) for nid, counter in counts.items()},
            "requests": {
                "annualLeaveApplied": len(annual_leave),
                "preferredOffRequested": len(preferred_off),
                "preferredOffApplied": sum(1 for key in preferred_off if assignments.get(key) == "O"),
                "preferredShiftRequested": len(preferred_shift),
                "preferredShiftApplied": sum(1 for key, duty in preferred_shift.items() if assignments.get(key) == duty),
            },
        }

    @staticmethod
    def _eligible(nurse, duty):
        if nurse["duty_role"] == "CHARGE" and duty != "D":
            return False
        if nurse["status"] == "PREGNANT" and duty == "N":
            return False
        return True

    @staticmethod
    def _night_streak(assignments, nurse_id, work_day):
        streak = 0
        cursor = work_day - timedelta(days=1)
        while assignments.get((nurse_id, cursor.isoformat())) == "N":
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    @staticmethod
    def _active_on(nurse, work_day: date):
        start = parse_date(nurse["affiliation_start"])
        end = parse_date(nurse["affiliation_end"]) if nurse["affiliation_end"] else None
        return start <= work_day and (end is None or work_day <= end) and nurse["status"] != "RETIRED"

    def _schedule_nurses(self, ward_id, year_month):
        year, month = parse_year_month(year_month)
        start = date(year, month, 1).isoformat()
        end = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
        with self.db.connection() as conn:
            return conn.execute(
                """SELECT * FROM nurses WHERE ward_id=? AND affiliation_start<=?
                   AND (affiliation_end IS NULL OR affiliation_end>=?) ORDER BY name""",
                (ward_id, end, start),
            ).fetchall()

    def _schedule_row(self, ward_id, schedule_id):
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM schedules WHERE id=? AND ward_id=?", (schedule_id, ward_id)
            ).fetchone()
        require(row is not None, 404, "SCHEDULE_NOT_FOUND", "근무표를 찾을 수 없습니다.")
        return row

    def _nurse_row(self, ward_id, nurse_id):
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM nurses WHERE id=? AND ward_id=?", (nurse_id, ward_id)
            ).fetchone()
        require(row is not None, 404, "NURSE_NOT_FOUND", "간호사를 찾을 수 없습니다.")
        return row

    def _validate_nurse(self, body, partial):
        required = {"name", "dutyRole", "status", "joinedAt", "careerMonths", "skillLevel", "affiliationStart"}
        if not partial:
            missing = required - set(body)
            require(not missing, 400, "VALIDATION_ERROR", f"필수 필드가 없습니다: {sorted(missing)}")
        if "name" in body:
            require(isinstance(body["name"], str) and 1 <= len(body["name"].strip()) <= 50,
                    400, "VALIDATION_ERROR", "name은 1자부터 50자여야 합니다.")
        if "dutyRole" in body:
            require(body["dutyRole"] in VALID_DUTY_ROLES, 400, "VALIDATION_ERROR", "dutyRole이 올바르지 않습니다.")
        if "status" in body:
            require(body["status"] in VALID_STATUSES, 400, "VALIDATION_ERROR", "status가 올바르지 않습니다.")
        if "joinedAt" in body:
            parse_date(body["joinedAt"], "joinedAt")
        if "affiliationStart" in body:
            parse_date(body["affiliationStart"], "affiliationStart")
        if body.get("affiliationEnd"):
            parse_date(body["affiliationEnd"], "affiliationEnd")
        if "careerMonths" in body:
            require(isinstance(body["careerMonths"], int) and body["careerMonths"] >= 0,
                    400, "VALIDATION_ERROR", "careerMonths는 0 이상 정수여야 합니다.")
        if "skillLevel" in body:
            require(isinstance(body["skillLevel"], int) and 1 <= body["skillLevel"] <= 5,
                    400, "VALIDATION_ERROR", "skillLevel은 1부터 5여야 합니다.")
        if "affiliationStart" in body:
            self._validate_affiliation(body["affiliationStart"], body.get("affiliationEnd"))

    @staticmethod
    def _validate_affiliation(start, end):
        if end:
            require(parse_date(start) <= parse_date(end), 422, "INVALID_AFFILIATION_RANGE", "소속 종료일은 시작일보다 빠를 수 없습니다.")

    def _validate_cell_change(self, conn, ward_id, schedule, change):
        require(set(change) >= {"nurseId", "date", "dutyCode"}, 400, "VALIDATION_ERROR", "셀 변경 필드가 부족합니다.")
        require(change["dutyCode"] in VALID_DUTIES, 400, "INVALID_DUTY_CODE", "지원하지 않는 듀티 코드입니다.")
        require(change["dutyCode"] != "AL", 422, "PROTECTED_CELL", "AL은 연차 승인으로만 입력할 수 있습니다.")
        work_day = parse_date(change["date"])
        require(change["date"][:7] == schedule["year_month"], 422, "DATE_OUT_OF_SCHEDULE", "근무표 대상 월 밖의 날짜입니다.")
        nurse = conn.execute("SELECT * FROM nurses WHERE id=? AND ward_id=?", (change["nurseId"], ward_id)).fetchone()
        require(nurse is not None, 404, "NURSE_NOT_FOUND", "간호사를 찾을 수 없습니다.")
        require(self._active_on(nurse, work_day), 422, "OUTSIDE_AFFILIATION", "소속 기간 밖에는 배정할 수 없습니다.")

    def _violations_for_nurse(self, ward_id, nurse_id):
        with self.db.connection() as conn:
            schedules = conn.execute(
                "SELECT id FROM schedules WHERE ward_id=? AND status IN ('DRAFT','CONFIRMED')", (ward_id,)
            ).fetchall()
            actor = conn.execute(
                "SELECT user_id FROM memberships WHERE ward_id=? AND role='HEAD_NURSE' LIMIT 1", (ward_id,)
            ).fetchone()
        if not actor:
            return []
        result = []
        for schedule in schedules:
            result.extend(v for v in self.violations(actor["user_id"], schedule["id"]) if v.get("nurseId") == nurse_id)
        return result

    @staticmethod
    def _violation(rule, nurse_id, work_date, current, message):
        return {
            "id": f"{rule}:{nurse_id}:{work_date}", "severity": "HARD", "ruleId": rule,
            "nurseId": nurse_id, "date": work_date, "currentValue": current, "message": message,
        }

    @staticmethod
    def _audit(conn, actor, action, target_type, target_id, before, after):
        conn.execute(
            "INSERT INTO audit_logs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id(), actor["ward_id"], actor["user_id"], action, target_type, target_id,
             json.dumps(before, ensure_ascii=False) if before is not None else None,
             json.dumps(after, ensure_ascii=False) if after is not None else None, now_iso()),
        )
