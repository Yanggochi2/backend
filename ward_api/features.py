from __future__ import annotations

import io
import csv
import hashlib
import json
import os
import sqlite3
import zipfile
from datetime import date, datetime, timedelta, timezone
from xml.sax.saxutils import escape
from xml.etree import ElementTree

from .db import json_value, new_id, now_iso
from .errors import ApiError, require
from .service import WardService, nurse_from_row, parse_date, parse_year_month


DEFAULT_RULES = (
    ("MIN_COVERAGE", "듀티별 최소 인원", "HARD", {"source": "ward.requiredStaff"}),
    ("CHARGE_DAY_ONLY", "차지 간호사 데이 근무", "HARD", {}),
    ("PREGNANT_NO_NIGHT", "임신 간호사 나이트 제외", "HARD", {}),
    ("NEW_NIGHT_SUPPORT", "신입 나이트 지원 인력", "HARD", {}),
    ("MAX_CONSECUTIVE_NIGHTS", "연속 나이트 제한", "HARD", {"max": 3}),
)


def _page(params: dict) -> tuple[int, int]:
    page = max(int(params.get("page", 0)), 0)
    size = min(max(int(params.get("size", 20)), 1), 100)
    return page, size


class FeatureWardService(WardService):
    """프론트엔드 API 명세에서 사용하는 병동 운영 기능."""

    def create_ward(self, actor_id: str, body: dict):
        result = super().create_ward(actor_id, body)
        self._ensure_rules(result["id"])
        return result

    def begin_idempotency(self, actor_id: str, key: str, method: str, path: str, body) -> tuple[str | None, dict | None]:
        require(1 <= len(key) <= 200, 400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key 길이가 올바르지 않습니다.")
        normalized = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=self._json_default)
        request_hash = hashlib.sha256(normalized.encode()).hexdigest()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM idempotency_records WHERE actor_id=? AND idempotency_key=?",
                (actor_id, key),
            ).fetchone()
            if row:
                require(row["method"] == method and row["request_path"] == path and row["request_hash"] == request_hash,
                        409, "IDEMPOTENCY_KEY_REUSED", "같은 Idempotency-Key가 다른 요청에 사용되었습니다.")
                require(row["status"] == "COMPLETED", 409, "IDEMPOTENCY_IN_PROGRESS", "같은 요청이 처리 중입니다.")
                return None, {"status": row["response_status"], "payload": json_value(row["response_json"], None)}
            record_id = new_id()
            conn.execute(
                """INSERT INTO idempotency_records(id,actor_id,idempotency_key,method,request_path,request_hash,status,created_at)
                   VALUES(?,?,?,?,?,?,'PROCESSING',?)""",
                (record_id, actor_id, key, method, path, request_hash, now_iso()),
            )
        return record_id, None

    def complete_idempotency(self, record_id: str, status: int, payload):
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE idempotency_records SET status='COMPLETED',response_status=?,response_json=?,completed_at=?
                   WHERE id=? AND status='PROCESSING'""",
                (status, json.dumps(payload, ensure_ascii=False), now_iso(), record_id),
            )

    def abandon_idempotency(self, record_id: str):
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM idempotency_records WHERE id=? AND status='PROCESSING'", (record_id,))

    @staticmethod
    def _json_default(value):
        if isinstance(value, bytes):
            return {"bytesSha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
        raise TypeError(f"지원하지 않는 멱등성 본문 타입: {type(value).__name__}")

    def request_membership(self, actor_id: str, body: dict):
        code = str(body.get("joinCode", "")).strip().upper()
        require(code, 400, "VALIDATION_ERROR", "joinCode가 필요합니다.")
        with self.db.transaction() as conn:
            active = conn.execute("SELECT 1 FROM memberships WHERE user_id=? AND status='ACTIVE'", (actor_id,)).fetchone()
            require(active is None, 409, "MEMBERSHIP_ALREADY_EXISTS", "이미 소속된 병동이 있습니다.")
            ward = conn.execute("SELECT * FROM wards WHERE upper(join_code)=?", (code,)).fetchone()
            require(ward is not None, 404, "JOIN_CODE_NOT_FOUND", "병동 코드를 찾을 수 없습니다.")
            duplicate = conn.execute(
                "SELECT 1 FROM membership_requests WHERE user_id=? AND ward_id=? AND status='PENDING'",
                (actor_id, ward["id"]),
            ).fetchone()
            require(duplicate is None, 409, "REQUEST_ALREADY_EXISTS", "이미 승인 대기 중인 신청이 있습니다.")
            request_id, requested_at = new_id(), now_iso()
            conn.execute(
                "INSERT INTO membership_requests(id,ward_id,user_id,status,requested_at) VALUES(?,?,?,'PENDING',?)",
                (request_id, ward["id"], actor_id, requested_at),
            )
        return {"id": request_id, "status": "PENDING", "requestedAt": requested_at}

    def list_membership_requests(self, actor_id: str, params: dict):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        page, size = _page(params)
        where, values = ["r.ward_id=?"], [actor["ward_id"]]
        if params.get("status"):
            where.append("r.status=?")
            values.append(params["status"])
        clause = " AND ".join(where)
        with self.db.connection() as conn:
            total = conn.execute(f"SELECT COUNT(*) n FROM membership_requests r WHERE {clause}", values).fetchone()["n"]
            rows = conn.execute(
                f"""SELECT r.*,u.name applicant_name FROM membership_requests r JOIN users u ON u.id=r.user_id
                    WHERE {clause} ORDER BY r.requested_at DESC LIMIT ? OFFSET ?""",
                (*values, size, page * size),
            ).fetchall()
        return [self._membership_request(r) for r in rows], self._meta(page, size, total)

    def process_membership_request(self, actor_id: str, request_id: str, approve: bool, reason: str | None = None):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        if not approve:
            require(isinstance(reason, str) and reason.strip(), 400, "REASON_REQUIRED", "반려 사유가 필요합니다.")
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT r.*,u.name applicant_name FROM membership_requests r JOIN users u ON u.id=r.user_id WHERE r.id=? AND r.ward_id=?",
                (request_id, actor["ward_id"]),
            ).fetchone()
            require(row is not None, 404, "RESOURCE_NOT_FOUND", "가입 신청을 찾을 수 없습니다.")
            require(row["status"] == "PENDING", 409, "REQUEST_ALREADY_PROCESSED", "이미 처리된 가입 신청입니다.")
            processed_at = now_iso()
            status = "APPROVED" if approve else "REJECTED"
            conn.execute(
                "UPDATE membership_requests SET status=?,processed_at=?,processor_id=?,rejection_reason=? WHERE id=?",
                (status, processed_at, actor_id, None if approve else reason.strip(), request_id),
            )
            if approve:
                membership_id = new_id()
                conn.execute(
                    "INSERT INTO memberships(id,user_id,ward_id,role,status,joined_at) VALUES(?,?,?,'NURSE','ACTIVE',?)",
                    (membership_id, row["user_id"], actor["ward_id"], processed_at),
                )
                if conn.execute("SELECT 1 FROM nurses WHERE id=?", (row["user_id"],)).fetchone() is None:
                    today = date.today().isoformat()
                    conn.execute(
                        """INSERT INTO nurses(id,ward_id,name,role,duty_role,status,joined_at,career_months,skill_level,
                           affiliation_start,affiliation_end,preceptor_of,version,created_at,updated_at)
                           VALUES(?,?,?,'NURSE','GENERAL','ACTIVE',?,0,3,?,NULL,'[]',1,?,?)""",
                        (row["user_id"], actor["ward_id"], row["applicant_name"], today, today, processed_at, processed_at),
                    )
                result = conn.execute("SELECT * FROM memberships WHERE id=?", (membership_id,)).fetchone()
                self._notify(conn, row["user_id"], "MEMBERSHIP_APPROVED", "병동 가입이 승인됐어요", "이제 병동 서비스를 이용할 수 있어요.", "MEMBERSHIP_REQUEST", request_id)
                return self._membership(result)
            updated = conn.execute(
                "SELECT r.*,u.name applicant_name FROM membership_requests r JOIN users u ON u.id=r.user_id WHERE r.id=?",
                (request_id,),
            ).fetchone()
            self._notify(conn, row["user_id"], "MEMBERSHIP_REJECTED", "병동 가입이 반려됐어요", reason.strip(), "MEMBERSHIP_REQUEST", request_id)
        return self._membership_request(updated)

    def get_join_code(self, actor_id: str):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        with self.db.connection() as conn:
            code = conn.execute("SELECT join_code FROM wards WHERE id=?", (actor["ward_id"],)).fetchone()["join_code"]
        return {"code": code}

    def rotate_join_code(self, actor_id: str):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        code = new_id().replace("-", "")[:8].upper()
        with self.db.transaction() as conn:
            conn.execute("UPDATE wards SET join_code=? WHERE id=?", (code, actor["ward_id"]))
            self._audit(conn, actor, "JOIN_CODE_ROTATED", "WARD", actor["ward_id"], None, {"code": code})
        return {"code": code}

    def grant_head_nurse(self, actor_id: str, nurse_id: str):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        with self.db.transaction() as conn:
            nurse = conn.execute("SELECT * FROM nurses WHERE id=? AND ward_id=?", (nurse_id, actor["ward_id"])).fetchone()
            require(nurse is not None, 404, "NURSE_NOT_FOUND", "간호사를 찾을 수 없습니다.")
            membership = conn.execute("SELECT * FROM memberships WHERE user_id=? AND ward_id=?", (nurse_id, actor["ward_id"])).fetchone()
            require(membership is not None, 409, "NURSE_ACCOUNT_NOT_LINKED", "가입 승인을 통해 연결된 간호사만 권한을 부여할 수 있습니다.")
            require(membership["role"] != "HEAD_NURSE", 409, "ROLE_ALREADY_ASSIGNED", "이미 수간호사입니다.")
            conn.execute("UPDATE memberships SET role='HEAD_NURSE' WHERE id=?", (membership["id"],))
            conn.execute("UPDATE nurses SET role='HEAD_NURSE',version=version+1,updated_at=? WHERE id=?", (now_iso(), nurse_id))
            updated = conn.execute("SELECT * FROM memberships WHERE id=?", (membership["id"],)).fetchone()
        return self._membership(updated)

    def transfer_head_nurse(self, actor_id: str, target_nurse_id: str):
        target = self.grant_head_nurse(actor_id, target_nurse_id)
        actor = self.actor_context(actor_id)
        with self.db.transaction() as conn:
            conn.execute("UPDATE memberships SET role='NURSE' WHERE user_id=? AND ward_id=?", (actor_id, actor["ward_id"]))
            conn.execute("UPDATE nurses SET role='NURSE',version=version+1,updated_at=? WHERE id=? AND ward_id=?", (now_iso(), actor_id, actor["ward_id"]))
        return {"previousHeadNurseId": actor_id, "newHeadNurse": target}

    def list_rules(self, actor_id: str):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        self._ensure_rules(actor["ward_id"])
        with self.db.connection() as conn:
            rows = conn.execute("SELECT * FROM rules WHERE ward_id=? ORDER BY name", (actor["ward_id"],)).fetchall()
        return [self._rule(r) for r in rows]

    def patch_rule(self, actor_id: str, rule_id: str, body: dict):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        allowed = {"enabled", "severity", "parameters", "reason"}
        require(not (set(body) - allowed), 400, "INVALID_RULE_VALUE", "지원하지 않는 규칙 필드입니다.")
        if "severity" in body:
            require(body["severity"] in {"HARD", "SOFT"}, 400, "INVALID_RULE_VALUE", "severity가 올바르지 않습니다.")
        if "enabled" in body:
            require(isinstance(body["enabled"], bool), 400, "INVALID_RULE_VALUE", "enabled는 boolean이어야 합니다.")
        if "parameters" in body:
            require(isinstance(body["parameters"], dict), 400, "INVALID_RULE_VALUE", "parameters는 객체여야 합니다.")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM rules WHERE id=? AND ward_id=?", (rule_id, actor["ward_id"])).fetchone()
            require(row is not None, 404, "RESOURCE_NOT_FOUND", "규칙을 찾을 수 없습니다.")
            enabled = int(body.get("enabled", bool(row["enabled"])))
            severity = body.get("severity", row["severity"])
            parameters = json.dumps(body.get("parameters", json_value(row["parameters"], {})), ensure_ascii=False)
            conn.execute("UPDATE rules SET enabled=?,severity=?,parameters=?,version=version+1 WHERE id=?", (enabled, severity, parameters, rule_id))
            updated = conn.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
            self._audit(conn, actor, "RULE_UPDATED", "RULE", rule_id, self._rule(row), self._rule(updated))
        return {**self._rule(updated), "violationSummary": []}

    def apply_rule_preset(self, actor_id: str, preset_id: str):
        require(preset_id.upper() in {"GENERAL", "STRICT"}, 404, "RESOURCE_NOT_FOUND", "규칙 프리셋을 찾을 수 없습니다.")
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        self._ensure_rules(actor["ward_id"])
        with self.db.transaction() as conn:
            conn.execute("UPDATE rules SET enabled=1,severity='HARD',version=version+1 WHERE ward_id=?", (actor["ward_id"],))
        return self.list_rules(actor_id)

    def list_holidays(self, actor_id: str, year_month: str):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        parse_year_month(year_month)
        with self.db.connection() as conn:
            rows = conn.execute("SELECT * FROM holidays WHERE ward_id=? AND holiday_date LIKE ? ORDER BY holiday_date", (actor["ward_id"], f"{year_month}%")).fetchall()
        return [{"date": r["holiday_date"], "isHoliday": bool(r["is_holiday"]), "name": r["name"]} for r in rows]

    def put_holiday(self, actor_id: str, holiday_date: str, body: dict):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        parse_date(holiday_date)
        require(isinstance(body.get("isHoliday"), bool), 400, "VALIDATION_ERROR", "isHoliday가 필요합니다.")
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO holidays(ward_id,holiday_date,is_holiday,name) VALUES(?,?,?,?)
                   ON CONFLICT(ward_id,holiday_date) DO UPDATE SET is_holiday=excluded.is_holiday,name=excluded.name""",
                (actor["ward_id"], holiday_date, int(body["isHoliday"]), body.get("name")),
            )
        return {"date": holiday_date, "isHoliday": body["isHoliday"], "name": body.get("name")}

    def get_off_target(self, actor_id: str, year_month: str):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        year, month = parse_year_month(year_month)
        with self.db.connection() as conn:
            row = conn.execute("SELECT target_count FROM off_targets WHERE ward_id=? AND year_month=?", (actor["ward_id"], year_month)).fetchone()
        default = max(date(year, month + 1, 1).day if month < 12 else 8, 8)
        return {"yearMonth": year_month, "targetCount": row["target_count"] if row else default}

    def patch_off_target(self, actor_id: str, year_month: str, body: dict):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        parse_year_month(year_month)
        target = body.get("targetCount")
        require(isinstance(target, int) and target >= 0, 400, "VALIDATION_ERROR", "targetCount는 0 이상 정수여야 합니다.")
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO off_targets VALUES(?,?,?) ON CONFLICT(ward_id,year_month) DO UPDATE SET target_count=excluded.target_count",
                (actor["ward_id"], year_month, target),
            )
        return {"yearMonth": year_month, "targetCount": target}

    def create_work_request(self, actor_id: str, body: dict):
        actor = self.actor_context(actor_id)
        request_type = body.get("type")
        dates = body.get("targetDates")
        require(request_type in {"ANNUAL_LEAVE", "PREFERRED_OFF", "PREFERRED_SHIFT"}, 400, "VALIDATION_ERROR", "type이 올바르지 않습니다.")
        require(isinstance(dates, list) and dates and len(dates) == len(set(dates)), 400, "VALIDATION_ERROR", "targetDates는 중복 없는 날짜 목록이어야 합니다.")
        parsed = [parse_date(d, "targetDates") for d in dates]
        require(len({(d.year, d.month) for d in parsed}) == 1, 422, "BUSINESS_RULE_VIOLATION", "신청 날짜는 같은 월이어야 합니다.")
        require(all(d >= date.today() for d in parsed), 422, "REQUEST_DATE_IN_PAST", "지난 날짜는 근무 신청을 할 수 없습니다.")
        reason_code = body.get("reasonCode")
        require(isinstance(reason_code, str) and reason_code, 400, "VALIDATION_ERROR", "reasonCode가 필요합니다.")
        if reason_code == "ETC":
            require(isinstance(body.get("reasonDetail"), str) and body["reasonDetail"].strip(), 400, "VALIDATION_ERROR", "기타 사유를 입력해야 합니다.")
        if request_type == "PREFERRED_SHIFT":
            require(body.get("preferredDuty") in {"D", "E", "N"}, 400, "VALIDATION_ERROR", "preferredDuty가 필요합니다.")
        request_id, created_at = new_id(), now_iso()
        with self.db.transaction() as conn:
            self._ensure_user_nurse(conn, actor_id, actor["ward_id"])
            self._validate_work_request_targets(conn, actor["ward_id"], actor_id, dates)
            active_requests = conn.execute(
                """SELECT type,target_dates FROM work_requests
                   WHERE ward_id=? AND applicant_id=? AND status IN ('PENDING','APPROVED')""",
                (actor["ward_id"], actor_id),
            ).fetchall()
            requested_dates = set(dates)
            conflict = next((r for r in active_requests if requested_dates.intersection(json_value(r["target_dates"], []))), None)
            require(conflict is None, 409, "REQUEST_DATE_CONFLICT", "같은 날짜에 대기 중이거나 승인된 신청이 있습니다.")
            duplicate = conn.execute(
                "SELECT 1 FROM work_requests WHERE ward_id=? AND applicant_id=? AND status='PENDING' AND target_dates=? AND type=?",
                (actor["ward_id"], actor_id, json.dumps(sorted(dates)), request_type),
            ).fetchone()
            require(duplicate is None, 409, "DUPLICATE_REQUEST", "동일한 신청이 이미 있습니다.")
            conn.execute(
                """INSERT INTO work_requests(id,ward_id,applicant_id,type,target_dates,reason_code,reason_detail,
                   preferred_duty,status,created_at) VALUES(?,?,?,?,?,?,?,?,'PENDING',?)""",
                (request_id, actor["ward_id"], actor_id, request_type, json.dumps(sorted(dates)), reason_code,
                 body.get("reasonDetail"), body.get("preferredDuty"), created_at),
            )
            row = conn.execute("SELECT * FROM work_requests WHERE id=?", (request_id,)).fetchone()
            self._audit(conn, actor, "REQUEST_CREATED", "WORK_REQUEST", request_id, None, self._work_request(row, True))
        return self._work_request(row, True)

    def list_work_requests(self, actor_id: str, params: dict, mine: bool = False):
        actor = self.actor_context(actor_id)
        if not mine:
            self.require_head(actor)
        page, size = _page(params)
        where, values = ["ward_id=?"], [actor["ward_id"]]
        if mine:
            where.append("applicant_id=?")
            values.append(actor_id)
        for key, column in (("applicantId", "applicant_id"), ("type", "type"), ("status", "status")):
            if params.get(key):
                where.append(f"{column}=?")
                values.append(params[key])
        if params.get("yearMonth"):
            parse_year_month(params["yearMonth"])
            where.append("target_dates LIKE ?")
            values.append(f'%"{params["yearMonth"]}-%')
        clause = " AND ".join(where)
        with self.db.connection() as conn:
            total = conn.execute(f"SELECT COUNT(*) n FROM work_requests WHERE {clause}", values).fetchone()["n"]
            rows = conn.execute(f"SELECT * FROM work_requests WHERE {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?", (*values, size, page * size)).fetchall()
        sensitive = actor["role"] == "HEAD_NURSE"
        return [self._work_request(r, sensitive or r["applicant_id"] == actor_id) for r in rows], self._meta(page, size, total)

    def get_work_request(self, actor_id: str, request_id: str):
        actor = self.actor_context(actor_id)
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM work_requests WHERE id=? AND ward_id=?", (request_id, actor["ward_id"])).fetchone()
        require(row is not None, 404, "RESOURCE_NOT_FOUND", "신청을 찾을 수 없습니다.")
        require(actor["role"] == "HEAD_NURSE" or row["applicant_id"] == actor_id, 404, "RESOURCE_NOT_FOUND", "신청을 찾을 수 없습니다.")
        return self._work_request(row, actor["role"] == "HEAD_NURSE" or row["applicant_id"] == actor_id)

    def process_work_request(self, actor_id: str, request_id: str, action: str, reason: str | None = None):
        actor = self.actor_context(actor_id)
        if action in {"approve", "reject"}:
            self.require_head(actor)
        if action == "reject":
            require(isinstance(reason, str) and reason.strip(), 400, "REASON_REQUIRED", "반려 사유가 필요합니다.")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM work_requests WHERE id=? AND ward_id=?", (request_id, actor["ward_id"])).fetchone()
            require(row is not None, 404, "RESOURCE_NOT_FOUND", "신청을 찾을 수 없습니다.")
            require(row["status"] == "PENDING", 409, "REQUEST_ALREADY_PROCESSED", "이미 처리된 신청입니다.")
            if action == "approve":
                self._ensure_user_nurse(conn, row["applicant_id"], actor["ward_id"])
                self._validate_work_request_targets(
                    conn, actor["ward_id"], row["applicant_id"], json_value(row["target_dates"], [])
                )
            if action == "cancel":
                require(row["applicant_id"] == actor_id, 404, "RESOURCE_NOT_FOUND", "신청을 찾을 수 없습니다.")
                status, processor = "CANCELLED", None
            else:
                status, processor = ("APPROVED" if action == "approve" else "REJECTED"), actor_id
            processed_at = now_iso()
            conn.execute(
                "UPDATE work_requests SET status=?,processor_id=?,processed_at=?,rejection_reason=? WHERE id=?",
                (status, processor, processed_at, reason.strip() if reason else None, request_id),
            )
            if status == "APPROVED" and row["type"] == "ANNUAL_LEAVE":
                self._ensure_user_nurse(conn, row["applicant_id"], actor["ward_id"])
                for target_date in json_value(row["target_dates"], []):
                    schedule = conn.execute("SELECT * FROM schedules WHERE ward_id=? AND year_month=?", (actor["ward_id"], target_date[:7])).fetchone()
                    if schedule:
                        require(schedule["status"] == "DRAFT", 409, "SCHEDULE_CONFIRMED", "확정된 근무표의 신청은 승인할 수 없습니다.")
                        conn.execute(
                            """INSERT INTO schedule_cells(schedule_id,nurse_id,work_date,duty_code) VALUES(?,?,?,'AL')
                               ON CONFLICT(schedule_id,nurse_id,work_date) DO UPDATE SET duty_code='AL'""",
                            (schedule["id"], row["applicant_id"], target_date),
                        )
            updated = conn.execute("SELECT * FROM work_requests WHERE id=?", (request_id,)).fetchone()
            if status in {"APPROVED", "REJECTED"}:
                self._notify(conn, row["applicant_id"], f"REQUEST_{status}", "근무 신청 결과가 도착했어요", status, "WORK_REQUEST", request_id)
            self._audit(conn, actor, f"REQUEST_{status}", "WORK_REQUEST", request_id, self._work_request(row, True), self._work_request(updated, True))
        return self._work_request(updated, True)

    @staticmethod
    def _ensure_user_nurse(conn, user_id: str, ward_id: str):
        if conn.execute("SELECT 1 FROM nurses WHERE id=? AND ward_id=?", (user_id, ward_id)).fetchone():
            return
        user = conn.execute(
            """SELECT u.name,m.role,m.joined_at FROM users u JOIN memberships m ON m.user_id=u.id
               WHERE u.id=? AND m.ward_id=? AND m.status='ACTIVE'""",
            (user_id, ward_id),
        ).fetchone()
        require(user is not None, 409, "NURSE_PROFILE_REQUIRED", "근무 신청 전에 병동 가입 승인이 필요합니다.")
        today, created_at = date.today().isoformat(), now_iso()
        duty_role = "CHARGE" if user["role"] == "HEAD_NURSE" else "GENERAL"
        conn.execute(
            """INSERT INTO nurses(id,ward_id,name,role,duty_role,status,joined_at,career_months,skill_level,
               affiliation_start,affiliation_end,preceptor_of,version,created_at,updated_at)
               VALUES(?,?,?,'NURSE',?,'ACTIVE',?,0,3,?,NULL,'[]',1,?,?)""",
            (user_id, ward_id, user["name"], duty_role, user["joined_at"] or today, today, created_at, created_at),
        )

    def _validate_work_request_targets(self, conn, ward_id: str, applicant_id: str, dates: list[str]):
        nurse = conn.execute("SELECT * FROM nurses WHERE id=? AND ward_id=?", (applicant_id, ward_id)).fetchone()
        require(nurse is not None, 409, "NURSE_PROFILE_REQUIRED", "근무 신청에 필요한 간호사 정보가 없습니다.")
        for target_date in dates:
            work_day = parse_date(target_date, "targetDates")
            require(self._active_on(nurse, work_day), 422, "OUTSIDE_AFFILIATION", "소속 기간 밖의 날짜는 신청할 수 없습니다.")
            schedule = conn.execute(
                "SELECT status FROM schedules WHERE ward_id=? AND year_month=?", (ward_id, target_date[:7])
            ).fetchone()
            require(schedule is None or schedule["status"] != "CONFIRMED", 409, "SCHEDULE_CONFIRMED", "이미 확정된 근무표의 날짜는 신청할 수 없습니다.")

    def notification_settings(self, actor_id: str, patch: dict | None = None):
        allowed = {"scheduleConfirmed", "scheduleCancelled", "requestResult", "dutyReminder", "webPush"}
        if patch is not None:
            require(not (set(patch) - allowed) and all(isinstance(v, bool) for v in patch.values()), 400, "VALIDATION_ERROR", "알림 설정 값이 올바르지 않습니다.")
        columns = {"scheduleConfirmed": "schedule_confirmed", "scheduleCancelled": "schedule_cancelled", "requestResult": "request_result", "dutyReminder": "duty_reminder", "webPush": "web_push"}
        with self.db.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO notification_settings(user_id) VALUES(?)", (actor_id,))
            if patch:
                assignments = ",".join(f"{columns[k]}=?" for k in patch)
                conn.execute(f"UPDATE notification_settings SET {assignments} WHERE user_id=?", (*[int(v) for v in patch.values()], actor_id))
            row = conn.execute("SELECT * FROM notification_settings WHERE user_id=?", (actor_id,)).fetchone()
        return {key: bool(row[column]) for key, column in columns.items()}

    def list_notifications(self, actor_id: str, params: dict):
        page, size = _page(params)
        where, values = ["user_id=?"], [actor_id]
        if str(params.get("unreadOnly", "false")).lower() == "true":
            where.append("is_read=0")
        if params.get("type"):
            where.append("type=?")
            values.append(params["type"])
        clause = " AND ".join(where)
        with self.db.connection() as conn:
            total = conn.execute(f"SELECT COUNT(*) n FROM notifications WHERE {clause}", values).fetchone()["n"]
            rows = conn.execute(f"SELECT * FROM notifications WHERE {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?", (*values, size, page * size)).fetchall()
        return [self._notification(r) for r in rows], self._meta(page, size, total)

    def mark_notification(self, actor_id: str, notification_id: str, body: dict):
        require(isinstance(body.get("read"), bool), 400, "VALIDATION_ERROR", "read가 필요합니다.")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM notifications WHERE id=? AND user_id=?", (notification_id, actor_id)).fetchone()
            require(row is not None, 404, "RESOURCE_NOT_FOUND", "알림을 찾을 수 없습니다.")
            conn.execute("UPDATE notifications SET is_read=? WHERE id=?", (int(body["read"]), notification_id))
            updated = conn.execute("SELECT * FROM notifications WHERE id=?", (notification_id,)).fetchone()
        return self._notification(updated)

    def audit_logs(self, actor_id: str, params: dict):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        page, size = _page(params)
        where, values = ["ward_id=?"], [actor["ward_id"]]
        for key, column in (("from", "occurred_at"), ("to", "occurred_at"), ("actorId", "actor_id"), ("actionType", "action_type")):
            if not params.get(key):
                continue
            if key == "from":
                where.append(f"{column}>=?")
            elif key == "to":
                where.append(f"{column}<=?")
            else:
                where.append(f"{column}=?")
            values.append(params[key])
        clause = " AND ".join(where)
        with self.db.connection() as conn:
            total = conn.execute(f"SELECT COUNT(*) n FROM audit_logs WHERE {clause}", values).fetchone()["n"]
            rows = conn.execute(
                f"""SELECT a.*,u.name actor_name FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id
                    WHERE {clause.replace('ward_id', 'a.ward_id').replace('occurred_at', 'a.occurred_at').replace('actor_id', 'a.actor_id').replace('action_type', 'a.action_type')}
                    ORDER BY a.occurred_at DESC LIMIT ? OFFSET ?""",
                (*values, size, page * size),
            ).fetchall()
        data = [{"id": r["id"], "occurredAt": r["occurred_at"], "actorId": r["actor_id"], "actorName": r["actor_name"] or "알 수 없음", "actionType": r["action_type"],
                 "targetType": r["target_type"], "targetId": r["target_id"], "before": json_value(r["before_json"], None), "after": json_value(r["after_json"], None)} for r in rows]
        return data, self._meta(page, size, total)

    def confirm_schedule(self, actor_id: str, schedule_id: str):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        schedule = self._schedule_row(actor["ward_id"], schedule_id)
        require(schedule["status"] == "DRAFT", 409, "INVALID_SCHEDULE_STATE", "DRAFT 근무표만 확정할 수 있습니다.")
        hard = [v for v in self.violations(actor_id, schedule_id) if v["severity"] == "HARD"]
        require(not hard, 409, "HARD_VIOLATIONS_EXIST", "하드 위반이 있어 확정할 수 없습니다.")
        confirmed_at = now_iso()
        with self.db.transaction() as conn:
            conn.execute("UPDATE schedules SET status='CONFIRMED',version=version+1,confirmed_at=?,updated_at=? WHERE id=?", (confirmed_at, confirmed_at, schedule_id))
            members = conn.execute("SELECT user_id FROM memberships WHERE ward_id=? AND status='ACTIVE'", (actor["ward_id"],)).fetchall()
            for member in members:
                self._notify(conn, member["user_id"], "SCHEDULE_CONFIRMED", "근무표가 확정됐어요", schedule["year_month"], "SCHEDULE", schedule_id)
            self._audit(conn, actor, "SCHEDULE_CONFIRMED", "SCHEDULE", schedule_id, {"status": "DRAFT"}, {"status": "CONFIRMED"})
        return self.get_schedule(actor_id, schedule_id)

    def cancel_schedule_confirmation(self, actor_id: str, schedule_id: str, reason: str | None):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        require(isinstance(reason, str) and reason.strip(), 400, "REASON_REQUIRED", "취소 사유가 필요합니다.")
        schedule = self._schedule_row(actor["ward_id"], schedule_id)
        require(schedule["status"] == "CONFIRMED", 409, "INVALID_SCHEDULE_STATE", "확정된 근무표만 취소할 수 있습니다.")
        with self.db.transaction() as conn:
            conn.execute("UPDATE schedules SET status='DRAFT',version=version+1,confirmed_at=NULL,updated_at=? WHERE id=?", (now_iso(), schedule_id))
            members = conn.execute("SELECT user_id FROM memberships WHERE ward_id=? AND status='ACTIVE'", (actor["ward_id"],)).fetchall()
            for member in members:
                self._notify(conn, member["user_id"], "SCHEDULE_CANCELLED", "근무표 확정이 취소됐어요", reason.strip(), "SCHEDULE", schedule_id)
        return self.get_schedule(actor_id, schedule_id)

    def stop_generation(self, actor_id: str, job_id: str, apply_best: bool):
        job = self.get_generation(actor_id, job_id)
        if job["status"] in {"SUCCEEDED", "FAILED", "NO_SOLUTION", "STOPPED"}:
            return job
        actor = self.actor_context(actor_id)
        with self.db.transaction() as conn:
            conn.execute("UPDATE generation_jobs SET status='STOPPED',stage='STOPPED',finished_at=? WHERE id=? AND ward_id=?", (now_iso(), job_id, actor["ward_id"]))
            conn.execute("UPDATE schedules SET status='DRAFT',updated_at=? WHERE id=?", (now_iso(), job["scheduleId"]))
        return self.get_schedule(actor_id, job["scheduleId"]) if apply_best else self.get_generation(actor_id, job_id)

    def relax_generation(self, actor_id: str, job_id: str, body: dict):
        job = self.get_generation(actor_id, job_id)
        require(isinstance(body.get("relaxationIds"), list), 400, "VALIDATION_ERROR", "relaxationIds가 필요합니다.")
        return self.generate_schedule(actor_id, job["scheduleId"], {"fixedCells": []})

    def apply_partial_generation(self, actor_id: str, job_id: str, body: dict):
        job = self.get_generation(actor_id, job_id)
        schedule = self.get_schedule(actor_id, job["scheduleId"])
        require(body.get("baseVersion") == schedule["version"], 409, "VERSION_CONFLICT", "근무표가 변경되었습니다.")
        return {**schedule, "violations": self.violations(actor_id, schedule["id"])}

    def export_schedule_xlsx(self, actor_id: str, schedule_id: str):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        schedule = self.get_schedule(actor_id, schedule_id)
        dates = sorted({c["date"] for c in schedule["cells"]})
        cells = {(c["nurseId"], c["date"]): c["dutyCode"] or "" for c in schedule["cells"]}
        rows = [["간호사", *dates]] + [[n["name"], *[cells.get((n["id"], d), "") for d in dates]] for n in schedule["nurses"]]
        return self._simple_xlsx(rows), f"schedule-{schedule['yearMonth']}.xlsx"

    def create_import_preview(self, actor_id: str, schedule_id: str, filename: str, content: bytes):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        schedule = self._schedule_row(actor["ward_id"], schedule_id)
        require(schedule["status"] == "DRAFT", 409, "INVALID_SCHEDULE_STATE", "DRAFT 근무표만 가져올 수 있습니다.")
        require(content, 422, "IMPORT_FILE_INVALID", "가져올 파일이 비어 있습니다.")
        require(len(content) <= 10 * 1024 * 1024, 413, "FILE_TOO_LARGE", "파일은 10MB 이하여야 합니다.")
        matrix = self._read_import_matrix(filename, content)
        require(len(matrix) >= 2 and len(matrix[0]) >= 2, 422, "IMPORT_FILE_INVALID", "첫 행에는 날짜, 첫 열에는 간호사 이름이 필요합니다.")
        headers = [str(value).strip() for value in matrix[0]]
        require(headers[0] in {"간호사", "이름", "nurse", "Nurse"}, 422, "IMPORT_FILE_INVALID", "첫 번째 열은 간호사 이름이어야 합니다.")
        dates = headers[1:]
        for value in dates:
            parse_date(value, "header.date")
            require(value[:7] == schedule["year_month"], 422, "DATE_OUT_OF_SCHEDULE", "근무표 대상 월 밖의 날짜가 포함되어 있습니다.")
        rows = []
        for row_number, values in enumerate(matrix[1:], 2):
            if not values or not str(values[0]).strip():
                continue
            nurse_name = str(values[0]).strip()
            for index, work_date in enumerate(dates, 1):
                raw_duty = str(values[index]).strip() if index < len(values) else ""
                if raw_duty:
                    rows.append({"rowNumber": row_number, "nurseName": nurse_name, "date": work_date, "rawDuty": raw_duty})

        with self.db.connection() as conn:
            nurses = conn.execute("SELECT id,name FROM nurses WHERE ward_id=?", (actor["ward_id"],)).fetchall()
        name_counts = {}
        for nurse in nurses:
            name_counts.setdefault(nurse["name"], []).append(nurse["id"])
        nurse_mappings = {name: ids[0] for name, ids in name_counts.items() if len(ids) == 1}
        duty_mappings = {code: code for code in ("D", "E", "N", "O", "OFF", "AL", "ED")}
        duty_mappings["OFF"] = "O"
        errors = self._import_errors(rows, nurse_mappings, duty_mappings)
        preview_id, created_at = new_id(), now_iso()
        status = "READY" if not errors else "NEEDS_MAPPING"
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO import_previews(id,ward_id,schedule_id,status,filename,rows_json,nurse_mappings,
                   duty_mappings,errors_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (preview_id, actor["ward_id"], schedule_id, status, filename, json.dumps(rows, ensure_ascii=False),
                 json.dumps(nurse_mappings, ensure_ascii=False), json.dumps(duty_mappings, ensure_ascii=False),
                 json.dumps(errors, ensure_ascii=False), created_at),
            )
        return self.get_import_preview(actor_id, preview_id, schedule_id)

    def update_import_preview(self, actor_id: str, schedule_id: str, preview_id: str, body: dict):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM import_previews WHERE id=? AND schedule_id=? AND ward_id=?", (preview_id, schedule_id, actor["ward_id"])).fetchone()
            require(row is not None, 404, "RESOURCE_NOT_FOUND", "가져오기 미리보기를 찾을 수 없습니다.")
            require(row["status"] != "APPLIED", 409, "INVALID_RESOURCE_STATE", "이미 적용된 미리보기입니다.")
            nurse_mappings = self._mapping_dict(body.get("nurseMappings", json_value(row["nurse_mappings"], {})), "sourceName", "nurseId")
            duty_mappings = self._mapping_dict(body.get("dutyMappings", json_value(row["duty_mappings"], {})), "sourceDuty", "dutyCode")
            rows = json_value(row["rows_json"], [])
            errors = self._import_errors(rows, nurse_mappings, duty_mappings)
            status = "READY" if not errors else "NEEDS_MAPPING"
            conn.execute(
                "UPDATE import_previews SET status=?,nurse_mappings=?,duty_mappings=?,errors_json=? WHERE id=?",
                (status, json.dumps(nurse_mappings, ensure_ascii=False), json.dumps(duty_mappings, ensure_ascii=False),
                 json.dumps(errors, ensure_ascii=False), preview_id),
            )
        return self.get_import_preview(actor_id, preview_id, schedule_id)

    def get_import_preview(self, actor_id: str, preview_id: str, schedule_id: str | None = None):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        with self.db.connection() as conn:
            if schedule_id is None:
                row = conn.execute("SELECT * FROM import_previews WHERE id=? AND ward_id=?", (preview_id, actor["ward_id"])).fetchone()
            else:
                row = conn.execute("SELECT * FROM import_previews WHERE id=? AND schedule_id=? AND ward_id=?", (preview_id, schedule_id, actor["ward_id"])).fetchone()
        require(row is not None, 404, "RESOURCE_NOT_FOUND", "가져오기 미리보기를 찾을 수 없습니다.")
        return self._import_preview(row)

    def apply_import_preview(self, actor_id: str, schedule_id: str, preview_id: str, body: dict, lock_token: str | None = None):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        require(isinstance(body.get("baseVersion"), int), 400, "VALIDATION_ERROR", "baseVersion이 필요합니다.")
        with self.db.transaction() as conn:
            preview = conn.execute("SELECT * FROM import_previews WHERE id=? AND schedule_id=? AND ward_id=?", (preview_id, schedule_id, actor["ward_id"])).fetchone()
            require(preview is not None, 404, "RESOURCE_NOT_FOUND", "가져오기 미리보기를 찾을 수 없습니다.")
            require(preview["status"] == "READY", 409, "IMPORT_PREVIEW_NOT_READY", "매핑 오류를 먼저 해결해야 합니다.")
            schedule = conn.execute("SELECT * FROM schedules WHERE id=? AND ward_id=?", (preview["schedule_id"], actor["ward_id"])).fetchone()
            require(schedule is not None, 404, "SCHEDULE_NOT_FOUND", "근무표를 찾을 수 없습니다.")
            require(schedule["status"] == "DRAFT", 409, "INVALID_SCHEDULE_STATE", "DRAFT 근무표만 가져올 수 있습니다.")
            require(schedule["version"] == body["baseVersion"], 409, "VERSION_CONFLICT", "근무표가 변경되었습니다.")
            self._check_lock(conn, actor, schedule["id"], lock_token)
            nurse_mappings = json_value(preview["nurse_mappings"], {})
            duty_mappings = json_value(preview["duty_mappings"], {})
            rows = json_value(preview["rows_json"], [])
            for item in rows:
                nurse_id = nurse_mappings[item["nurseName"]]
                duty = duty_mappings[item["rawDuty"]]
                nurse = conn.execute("SELECT * FROM nurses WHERE id=? AND ward_id=?", (nurse_id, actor["ward_id"])).fetchone()
                require(nurse is not None, 422, "IMPORT_MAPPING_INVALID", "다른 병동의 간호사가 매핑되어 있습니다.")
                work_day = parse_date(item["date"])
                require(self._active_on(nurse, work_day), 422, "OUTSIDE_AFFILIATION", "소속 기간 밖의 셀이 포함되어 있습니다.")
                conn.execute(
                    """INSERT INTO schedule_cells(schedule_id,nurse_id,work_date,duty_code) VALUES(?,?,?,?)
                       ON CONFLICT(schedule_id,nurse_id,work_date) DO UPDATE SET duty_code=excluded.duty_code""",
                    (schedule["id"], nurse_id, item["date"], duty),
                )
            conn.execute("UPDATE schedules SET version=version+1,updated_at=? WHERE id=?", (now_iso(), schedule["id"]))
            conn.execute("UPDATE import_previews SET status='APPLIED' WHERE id=?", (preview_id,))
            self._audit(conn, actor, "SCHEDULE_IMPORTED", "SCHEDULE", schedule["id"],
                        {"version": schedule["version"]}, {"version": schedule["version"] + 1, "cells": len(rows)})
        result = self.get_schedule(actor_id, schedule["id"])
        return {**result, "violations": self.violations(actor_id, schedule["id"])}

    def acquire_schedule_lock(self, actor_id: str, schedule_id: str):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        schedule = self._schedule_row(actor["ward_id"], schedule_id)
        require(schedule["status"] == "DRAFT", 409, "INVALID_SCHEDULE_STATE", "DRAFT 근무표만 잠글 수 있습니다.")
        token, acquired_at = new_id(), now_iso()
        expires_at = self._lock_expiry()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM schedule_locks WHERE schedule_id=? AND expires_at IS NOT NULL AND expires_at<=?", (schedule_id, now_iso()))
            try:
                conn.execute(
                    """INSERT INTO schedule_locks(schedule_id,ward_id,holder_user_id,lock_token,acquired_at,expires_at)
                       VALUES(?,?,?,?,?,?)""",
                    (schedule_id, actor["ward_id"], actor_id, token, acquired_at, expires_at),
                )
            except sqlite3.IntegrityError:
                raise ApiError(409, "LOCK_ALREADY_HELD", "이미 편집 잠금이 설정되어 있습니다.")
            self._audit(conn, actor, "SCHEDULE_LOCKED", "SCHEDULE", schedule_id, None, {"holderUserId": actor_id})
        return {"scheduleId": schedule_id, "holderUserId": actor_id, "token": token,
                "acquiredAt": acquired_at, "expiresAt": expires_at}

    def release_schedule_lock(self, actor_id: str, schedule_id: str, lock_token: str | None):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM schedule_locks WHERE schedule_id=? AND expires_at IS NOT NULL AND expires_at<=?", (schedule_id, now_iso()))
            row = conn.execute("SELECT * FROM schedule_locks WHERE schedule_id=? AND ward_id=?", (schedule_id, actor["ward_id"])).fetchone()
            require(row is not None, 404, "RESOURCE_NOT_FOUND", "편집 잠금을 찾을 수 없습니다.")
            require(row["holder_user_id"] == actor_id and row["lock_token"] == lock_token,
                    423, "SCHEDULE_LOCKED", "잠금 소유자와 토큰이 일치하지 않습니다.")
            conn.execute("DELETE FROM schedule_locks WHERE schedule_id=?", (schedule_id,))
            self._audit(conn, actor, "SCHEDULE_UNLOCKED", "SCHEDULE", schedule_id, {"holderUserId": actor_id}, None)

    def takeover_schedule_lock(self, actor_id: str, schedule_id: str, reason: str | None):
        actor = self.actor_context(actor_id)
        self.require_head(actor)
        require(isinstance(reason, str) and reason.strip(), 400, "REASON_REQUIRED", "잠금 인수 사유가 필요합니다.")
        self._schedule_row(actor["ward_id"], schedule_id)
        token, acquired_at = new_id(), now_iso()
        expires_at = self._lock_expiry()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM schedule_locks WHERE schedule_id=? AND expires_at IS NOT NULL AND expires_at<=?", (schedule_id, now_iso()))
            before = conn.execute("SELECT * FROM schedule_locks WHERE schedule_id=? AND ward_id=?", (schedule_id, actor["ward_id"])).fetchone()
            require(before is not None, 404, "RESOURCE_NOT_FOUND", "인수할 편집 잠금이 없습니다.")
            conn.execute("UPDATE schedule_locks SET holder_user_id=?,lock_token=?,acquired_at=?,expires_at=? WHERE schedule_id=?",
                         (actor_id, token, acquired_at, expires_at, schedule_id))
            self._audit(conn, actor, "SCHEDULE_LOCK_TAKEN_OVER", "SCHEDULE", schedule_id,
                        {"holderUserId": before["holder_user_id"]}, {"holderUserId": actor_id, "reason": reason.strip()})
        return {"scheduleId": schedule_id, "holderUserId": actor_id, "token": token,
                "acquiredAt": acquired_at, "expiresAt": expires_at}

    @staticmethod
    def _check_lock(conn, actor, schedule_id: str, lock_token: str | None):
        conn.execute("DELETE FROM schedule_locks WHERE schedule_id=? AND expires_at IS NOT NULL AND expires_at<=?", (schedule_id, now_iso()))
        row = conn.execute("SELECT * FROM schedule_locks WHERE schedule_id=? AND ward_id=?", (schedule_id, actor["ward_id"])).fetchone()
        if row:
            require(row["holder_user_id"] == actor["user_id"] and row["lock_token"] == lock_token,
                    423, "SCHEDULE_LOCKED", "다른 사용자가 근무표를 편집 중입니다.")

    @staticmethod
    def _lock_expiry():
        ttl = max(int(os.getenv("WARD_LOCK_TTL_SECONDS", "1800")), 60)
        return (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _mapping_dict(value, source_key: str, target_key: str):
        if isinstance(value, dict):
            return {str(k): str(v) for k, v in value.items() if k and v}
        if isinstance(value, list):
            result = {}
            for item in value:
                require(isinstance(item, dict) and item.get(source_key) and item.get(target_key),
                        400, "VALIDATION_ERROR", "매핑 항목 형식이 올바르지 않습니다.")
                result[str(item[source_key])] = str(item[target_key])
            return result
        raise ApiError(400, "VALIDATION_ERROR", "매핑은 객체 또는 목록이어야 합니다.")

    @staticmethod
    def _import_errors(rows, nurse_mappings, duty_mappings):
        errors = []
        for item in rows:
            if item["nurseName"] not in nurse_mappings:
                errors.append({"rowNumber": item["rowNumber"], "field": "nurseName", "value": item["nurseName"], "reason": "NURSE_MAPPING_REQUIRED"})
            duty = duty_mappings.get(item["rawDuty"])
            if duty not in {"D", "E", "N", "O", "AL", "ED"}:
                errors.append({"rowNumber": item["rowNumber"], "field": "dutyCode", "value": item["rawDuty"], "reason": "DUTY_MAPPING_REQUIRED"})
        return errors

    @staticmethod
    def _import_preview(row):
        rows = json_value(row["rows_json"], [])
        return {"id": row["id"], "scheduleId": row["schedule_id"], "status": row["status"],
                "filename": row["filename"], "rowCount": len(rows), "rows": rows[:100],
                "nurseMappings": json_value(row["nurse_mappings"], {}),
                "dutyMappings": json_value(row["duty_mappings"], {}),
                "errors": json_value(row["errors_json"], []), "createdAt": row["created_at"]}

    @staticmethod
    def _read_import_matrix(filename: str, content: bytes) -> list[list[str]]:
        if filename.lower().endswith(".csv"):
            return list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                shared = []
                if "xl/sharedStrings.xml" in archive.namelist():
                    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                    shared = ["".join(node.itertext()) for node in root]
                sheet_name = "xl/worksheets/sheet1.xml"
                require(sheet_name in archive.namelist(), 422, "IMPORT_FILE_INVALID", "첫 번째 워크시트를 찾을 수 없습니다.")
                root = ElementTree.fromstring(archive.read(sheet_name))
                namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                result = []
                for row in root.findall(".//x:sheetData/x:row", namespace):
                    values = []
                    for cell in row.findall("x:c", namespace):
                        ref = cell.attrib.get("r", "A1")
                        letters = "".join(ch for ch in ref if ch.isalpha())
                        column = 0
                        for letter in letters:
                            column = column * 26 + ord(letter.upper()) - 64
                        while len(values) < column:
                            values.append("")
                        kind = cell.attrib.get("t")
                        if kind == "inlineStr":
                            node = cell.find("x:is", namespace)
                            value = "".join(node.itertext()) if node is not None else ""
                        else:
                            node = cell.find("x:v", namespace)
                            value = node.text if node is not None and node.text else ""
                            if kind == "s" and value:
                                value = shared[int(value)]
                        values[column - 1] = value
                    result.append(values)
                return result
        except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, IndexError, UnicodeDecodeError) as exc:
            raise ApiError(422, "IMPORT_FILE_INVALID", "지원하지 않거나 손상된 파일입니다.") from exc

    def _ensure_rules(self, ward_id: str):
        with self.db.transaction() as conn:
            for code, name, severity, parameters in DEFAULT_RULES:
                conn.execute(
                    "INSERT OR IGNORE INTO rules(id,ward_id,code,name,severity,enabled,parameters,version) VALUES(?,?,?,?,?,1,?,1)",
                    (new_id(), ward_id, code, name, severity, json.dumps(parameters, ensure_ascii=False)),
                )

    @staticmethod
    def _rule(row):
        return {"id": row["id"], "code": row["code"], "name": row["name"], "severity": row["severity"],
                "enabled": bool(row["enabled"]), "parameters": json_value(row["parameters"], {}), "version": row["version"]}

    @staticmethod
    def _membership_request(row):
        return {"id": row["id"], "status": row["status"], "applicantName": row["applicant_name"],
                "requestedAt": row["requested_at"], "rejectionReason": row["rejection_reason"]}

    @staticmethod
    def _work_request(row, sensitive: bool):
        item = {"id": row["id"], "applicantId": row["applicant_id"], "type": row["type"],
                "targetDates": json_value(row["target_dates"], []), "reasonCode": row["reason_code"],
                "preferredDuty": row["preferred_duty"], "status": row["status"], "processorId": row["processor_id"],
                "processedAt": row["processed_at"], "rejectionReason": row["rejection_reason"]}
        if sensitive:
            item["reasonDetail"] = row["reason_detail"]
        return item

    @staticmethod
    def _notify(conn, user_id, kind, title, body, resource_type, resource_id):
        conn.execute("INSERT INTO notifications VALUES(?,?,?,?,?,0,?,?,?)", (new_id(), user_id, kind, title, body, now_iso(), resource_type, resource_id))

    @staticmethod
    def _notification(row):
        return {"id": row["id"], "type": row["type"], "title": row["title"], "body": row["body"],
                "read": bool(row["is_read"]), "createdAt": row["created_at"], "resourceType": row["resource_type"], "resourceId": row["resource_id"]}

    @staticmethod
    def _meta(page, size, total):
        return {"page": page, "size": size, "totalElements": total, "totalPages": (total + size - 1) // size}

    @staticmethod
    def _simple_xlsx(rows: list[list[str]]) -> bytes:
        def col_name(index: int) -> str:
            result = ""
            while index:
                index, rem = divmod(index - 1, 26)
                result = chr(65 + rem) + result
            return result

        sheet_rows = []
        for r_index, row in enumerate(rows, 1):
            cells = "".join(
                f'<c r="{col_name(c_index)}{r_index}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
                for c_index, value in enumerate(row, 1)
            )
            sheet_rows.append(f'<row r="{r_index}">{cells}</row>')
        sheet = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(sheet_rows) + "</sheetData></worksheet>"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
            archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            archive.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="근무표" sheetId="1" r:id="rId1"/></sheets></workbook>')
            archive.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
        return buffer.getvalue()
