from __future__ import annotations

import http.cookiejar
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from ward_api.server import create_server


class Client:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = None

    def call(self, method: str, path: str, body=None, extra_headers=None):
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if self.csrf and method not in {"GET", "HEAD", "OPTIONS"}:
            headers["X-CSRF-Token"] = self.csrf
        headers.update(extra_headers or {})
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        with self.opener.open(request) as response:
            raw = response.read()
            if response.status == 204:
                return None
            if response.headers.get_content_type() != "application/json":
                return raw
            return json.loads(raw)

    def upload(self, path: str, filename: str, content: bytes):
        boundary = "----WardApiBoundary"
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        headers = {
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-CSRF-Token": self.csrf,
        }
        request = urllib.request.Request(self.base_url + path, data=body, method="POST", headers=headers)
        with self.opener.open(request) as response:
            return json.loads(response.read())

    def signup_login(self, name: str, email: str):
        self.call("POST", "/auth/signup", {
            "name": name, "email": email, "password": "Passw0rd!", "termsAgreed": True,
        })
        result = self.call("POST", "/auth/login", {"email": email, "password": "Passw0rd!"})
        self.csrf = result["data"]["csrfToken"]


class ExtendedApiFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = create_server("127.0.0.1", 0, str(Path(self.temp.name) / "test.db"), seed_demo=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        base = f"http://127.0.0.1:{self.server.server_port}/api/v1"
        self.head = Client(base)
        self.nurse = Client(base)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def test_membership_rules_requests_notifications_and_schedule(self):
        self.head.signup_login("수간호사", "head@flow.test")
        ward = self.head.call("POST", "/wards", {
            "hospitalName": "통합병원", "wardName": "A병동",
            "requiredStaff": {"D": 2, "E": 2, "N": 1}, "rulePreset": "GENERAL",
        })["data"]
        self.assertEqual(ward["membership"]["role"], "HEAD_NURSE")
        join_code = self.head.call("GET", "/wards/me/join-code")["data"]["code"]

        self.nurse.signup_login("가입간호사", "nurse@flow.test")
        request = self.nurse.call("POST", "/ward-membership-requests", {"joinCode": join_code})["data"]
        pending = self.head.call("GET", "/wards/me/membership-requests?status=PENDING")["data"]
        self.assertEqual(pending[0]["id"], request["id"])
        membership = self.head.call("POST", f"/wards/me/membership-requests/{request['id']}/approve", {})["data"]
        self.assertEqual(membership["role"], "NURSE")
        self.assertEqual(self.nurse.call("GET", "/me")["data"]["ward"]["id"], ward["id"])

        settings = self.nurse.call("PATCH", "/me/notification-settings", {"dutyReminder": False})["data"]
        self.assertFalse(settings["dutyReminder"])

        rules = self.head.call("GET", "/wards/me/rules")["data"]
        self.assertGreaterEqual(len(rules), 5)
        changed_rule = self.head.call("PATCH", f"/wards/me/rules/{rules[0]['id']}", {"enabled": False, "reason": "테스트"})["data"]
        self.assertFalse(changed_rule["enabled"])
        self.head.call("PUT", "/wards/me/holidays/2026-10-09", {"isHoliday": True, "name": "한글날"})
        self.assertEqual(len(self.head.call("GET", "/wards/me/holidays?yearMonth=2026-10")["data"]), 1)
        off_target = self.head.call("PATCH", "/wards/me/off-targets/2026-10", {"targetCount": 9})["data"]
        self.assertEqual(off_target["targetCount"], 9)

        work_request = self.nurse.call("POST", "/wards/me/requests", {
            "type": "PREFERRED_OFF", "targetDates": ["2026-10-15"], "reasonCode": "PERSONAL",
        })["data"]
        listed = self.head.call("GET", "/wards/me/requests?yearMonth=2026-10&status=PENDING")["data"]
        self.assertEqual(listed[0]["id"], work_request["id"])
        self.head.call("POST", f"/wards/me/requests/{work_request['id']}/approve", {})
        annual_request = self.nurse.call("POST", "/wards/me/requests", {
            "type": "ANNUAL_LEAVE", "targetDates": ["2026-10-16"], "reasonCode": "PERSONAL",
        })["data"]
        self.head.call("POST", f"/wards/me/requests/{annual_request['id']}/approve", {})
        notifications = self.nurse.call("GET", "/me/notifications?unreadOnly=true")["data"]
        self.assertTrue(any(item["type"] == "REQUEST_APPROVED" for item in notifications))

        for index in range(5):
            self.head.call("POST", "/wards/me/nurses", {
                "name": f"추가간호사{index}", "dutyRole": "GENERAL", "status": "ACTIVE",
                "joinedAt": "2024-01-01", "careerMonths": 24 + index, "skillLevel": 3,
                "affiliationStart": "2024-01-01", "affiliationEnd": None, "preceptorOf": [],
            })
        schedule = self.head.call("POST", "/wards/me/schedules", {"yearMonth": "2026-10"})["data"]
        generation = self.head.call("POST", f"/wards/me/schedules/{schedule['id']}/generations", {"fixedCells": [], "maxSeconds": 10})["data"]
        self.assertEqual(generation["status"], "SUCCEEDED")
        generated_schedule = self.head.call("GET", f"/wards/me/schedules/{schedule['id']}")["data"]
        requested_cells = {(cell["nurseId"], cell["date"]): cell["dutyCode"] for cell in generated_schedule["cells"]}
        self.assertEqual(requested_cells[(membership["userId"], "2026-10-15")], "O")
        self.assertEqual(requested_cells[(membership["userId"], "2026-10-16")], "AL")
        exported_draft = self.head.call("GET", f"/wards/me/schedules/{schedule['id']}/export.xlsx")
        lock = self.head.call("POST", f"/wards/me/schedules/{schedule['id']}/lock", {})["data"]
        first_cell = generated_schedule["cells"][0]
        patch_body = {
            "baseVersion": generated_schedule["version"],
            "changes": [{"nurseId": first_cell["nurseId"], "date": first_cell["date"], "dutyCode": first_cell["dutyCode"]}],
        }
        with self.assertRaises(urllib.error.HTTPError) as locked:
            self.head.call("PATCH", f"/wards/me/schedules/{schedule['id']}/cells", patch_body)
        self.assertEqual(locked.exception.code, 423)
        locked.exception.close()
        taken_over = self.head.call(
            "POST", f"/wards/me/schedules/{schedule['id']}/lock/takeover", {"reason": "교대 근무 인수"},
        )["data"]
        self.assertNotEqual(lock["token"], taken_over["token"])
        lock = taken_over
        patched = self.head.call(
            "PATCH", f"/wards/me/schedules/{schedule['id']}/cells", patch_body,
            {"X-Schedule-Lock-Token": lock["token"]},
        )["data"]
        preview = self.head.upload(
            f"/wards/me/schedules/{schedule['id']}/import-previews", "schedule.xlsx", exported_draft,
        )["data"]
        self.assertEqual(preview["status"], "READY")
        preview = self.head.call(
            "PATCH", f"/wards/me/schedules/{schedule['id']}/import-previews/{preview['id']}",
            {"nurseMappings": preview["nurseMappings"], "dutyMappings": preview["dutyMappings"]},
        )["data"]
        self.assertEqual(preview["status"], "READY")
        imported = self.head.call(
            "POST", f"/wards/me/schedules/{schedule['id']}/import-previews/{preview['id']}/apply",
            {"baseVersion": patched["version"]}, {"X-Schedule-Lock-Token": lock["token"]},
        )["data"]
        self.assertEqual(imported["yearMonth"], "2026-10")
        self.head.call(
            "DELETE", f"/wards/me/schedules/{schedule['id']}/lock", None,
            {"X-Schedule-Lock-Token": lock["token"]},
        )
        confirmed = self.head.call("POST", f"/wards/me/schedules/{schedule['id']}/confirm", {"acknowledgedSoftViolationIds": []})["data"]
        self.assertEqual(confirmed["status"], "CONFIRMED")
        exported = self.head.call("GET", f"/wards/me/schedules/{schedule['id']}/export.xlsx")
        self.assertTrue(exported.startswith(b"PK"))
        audit = self.head.call("GET", "/wards/me/audit-logs?size=100")
        self.assertGreater(audit["meta"]["totalElements"], 0)

    def test_idempotency_replays_post_and_rejects_different_payload(self):
        self.head.signup_login("수간호사", "head@idempotency.test")
        self.head.call("POST", "/wards", {
            "hospitalName": "멱등병원", "wardName": "A병동",
            "requiredStaff": {"D": 1, "E": 1, "N": 1}, "rulePreset": "GENERAL",
        })
        body = {
            "name": "멱등간호사", "dutyRole": "GENERAL", "status": "ACTIVE",
            "joinedAt": "2024-01-01", "careerMonths": 24, "skillLevel": 3,
            "affiliationStart": "2024-01-01", "affiliationEnd": None, "preceptorOf": [],
        }
        headers = {"Idempotency-Key": "nurse-create-001"}
        first = self.head.call("POST", "/wards/me/nurses", body, headers)
        replayed = self.head.call("POST", "/wards/me/nurses", body, headers)
        self.assertEqual(first, replayed)
        self.assertEqual(self.head.call("GET", "/wards/me/nurses")["meta"]["totalElements"], 1)

        changed = {**body, "name": "다른간호사"}
        with self.assertRaises(urllib.error.HTTPError) as reused:
            self.head.call("POST", "/wards/me/nurses", changed, headers)
        self.assertEqual(reused.exception.code, 409)
        reused.exception.close()

    def test_request_rejects_past_and_overlapping_dates(self):
        self.head.signup_login("수간호사", "head@request-validation.test")
        self.head.call("POST", "/wards", {
            "hospitalName": "검증병원", "wardName": "A병동",
            "requiredStaff": {"D": 1, "E": 1, "N": 1}, "rulePreset": "GENERAL",
        })
        with self.assertRaises(urllib.error.HTTPError) as past:
            self.head.call("POST", "/wards/me/requests", {
                "type": "ANNUAL_LEAVE", "targetDates": ["2020-01-01"], "reasonCode": "PERSONAL",
            })
        self.assertEqual(past.exception.code, 422)
        past.exception.close()

        self.head.call("POST", "/wards/me/requests", {
            "type": "PREFERRED_OFF", "targetDates": ["2026-10-20"], "reasonCode": "PERSONAL",
        })
        with self.assertRaises(urllib.error.HTTPError) as overlap:
            self.head.call("POST", "/wards/me/requests", {
                "type": "ANNUAL_LEAVE", "targetDates": ["2026-10-20"], "reasonCode": "PERSONAL",
            })
        self.assertEqual(overlap.exception.code, 409)
        overlap.exception.close()


class ProductionConfigurationTest(unittest.TestCase):
    def test_production_disables_demo_and_enables_secure_defaults(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            "APP_ENV": "production",
            "WARD_ALLOWED_ORIGINS": "https://app.example.test",
            "WARD_DEV_USER_ID": "",
        }, clear=False):
            server = create_server("127.0.0.1", 0, str(Path(temp) / "production.db"), seed_demo=True)
            try:
                self.assertTrue(server.production)
                self.assertTrue(server.secure_cookies)
                self.assertIsNone(server.dev_user_id)
                self.assertTrue(server.service.async_generation)
                with server.service.db.connection() as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) n FROM users").fetchone()["n"], 0)
            finally:
                server.server_close()


if __name__ == "__main__":
    unittest.main()
