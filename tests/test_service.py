import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import http.cookiejar
from pathlib import Path

from ward_api.db import Database
from ward_api.errors import ApiError
from ward_api.server import WardHttpServer
from ward_api.service import WardService


def nurse_payload(name, duty_role="GENERAL", status="ACTIVE", skill=3):
    return {
        "name": name,
        "dutyRole": duty_role,
        "status": status,
        "joinedAt": "2024-01-01",
        "careerMonths": 36,
        "skillLevel": skill,
        "affiliationStart": "2024-01-01",
        "affiliationEnd": None,
        "preceptorOf": [],
    }


class WardServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")
        self.db.initialize()
        seed = self.db.seed_demo()
        self.actor_id = seed["userId"]
        self.service = WardService(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def add_full_team(self):
        specs = [
            ("김차지", "CHARGE", "ACTIVE"),
            ("이프리셉터", "PRECEPTOR", "ACTIVE"),
            ("박일반", "GENERAL", "ACTIVE"),
            ("최신입", "NEW", "ACTIVE"),
            ("정임신", "GENERAL", "PREGNANT"),
        ]
        return [self.service.create_nurse(self.actor_id, nurse_payload(*spec)) for spec in specs]

    def test_add_and_list_nurses(self):
        created = self.service.create_nurse(self.actor_id, nurse_payload("김간호사"))
        self.assertEqual(created["role"], "NURSE")
        self.assertEqual(created["name"], "김간호사")
        nurses, meta = self.service.list_nurses(self.actor_id, {"page": "0", "size": "20"})
        self.assertEqual(meta["totalElements"], 1)
        self.assertEqual(nurses[0]["id"], created["id"])

    def test_invalid_nurse_is_rejected(self):
        body = nurse_payload("잘못된 간호사")
        body["skillLevel"] = 7
        with self.assertRaises(ApiError) as error:
            self.service.create_nurse(self.actor_id, body)
        self.assertEqual(error.exception.code, "VALIDATION_ERROR")

    def test_create_and_generate_schedule(self):
        self.add_full_team()
        schedule = self.service.create_schedule(self.actor_id, "2026-10")
        self.assertEqual(schedule["status"], "DRAFT")
        job = self.service.generate_schedule(self.actor_id, schedule["id"], {})
        self.assertEqual(job["status"], "SUCCEEDED")
        generated = self.service.get_schedule(self.actor_id, schedule["id"])
        self.assertGreater(len(generated["cells"]), 0)
        self.assertTrue(all(item["status"] == "MET" for item in generated["coverage"]))
        hard = [v for v in generated["violations"] if v["severity"] == "HARD"]
        self.assertEqual(hard, [])

    def test_cell_patch_checks_version(self):
        nurses = self.add_full_team()
        schedule = self.service.create_schedule(self.actor_id, "2026-10")
        result = self.service.patch_cells(self.actor_id, schedule["id"], {
            "baseVersion": schedule["version"],
            "changes": [{"nurseId": nurses[0]["id"], "date": "2026-10-01", "dutyCode": "D"}],
        })
        self.assertEqual(result["version"], schedule["version"] + 1)
        with self.assertRaises(ApiError) as error:
            self.service.patch_cells(self.actor_id, schedule["id"], {
                "baseVersion": schedule["version"],
                "changes": [{"nurseId": nurses[1]["id"], "date": "2026-10-01", "dutyCode": "E"}],
            })
        self.assertEqual(error.exception.code, "VERSION_CONFLICT")


class HttpApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Database(Path(self.tmp.name) / "http.db")
        db.initialize()
        seed = db.seed_demo()
        self.server = WardHttpServer(("127.0.0.1", 0), WardService(db), seed["userId"])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/api/v1"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())

    def test_http_create_nurse_and_schedule(self):
        status, response = self.request("POST", "/wards/me/nurses", nurse_payload("API 간호사"))
        self.assertEqual(status, 201)
        self.assertEqual(response["data"]["name"], "API 간호사")
        status, response = self.request("POST", "/wards/me/schedules", {"yearMonth": "2026-11"})
        self.assertEqual(status, 201)
        self.assertEqual(response["data"]["yearMonth"], "2026-11")


class FullHttpFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Database(Path(self.tmp.name) / "flow.db")
        db.initialize()
        self.server = WardHttpServer(("127.0.0.1", 0), WardService(db), None, False, False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/api/v1"
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.csrf = None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, method, path, body=None, csrf=True):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        if csrf and self.csrf and method in ("POST", "PATCH", "DELETE"):
            headers["X-CSRF-Token"] = self.csrf
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        with self.opener.open(request) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None

    def test_signup_to_generated_schedule_flow(self):
        status, _ = self.request("POST", "/auth/signup", {
            "name": "플로우 수간호사", "email": "flow@example.com",
            "password": "flowpass123", "termsAgreed": True,
        }, csrf=False)
        self.assertEqual(status, 201)

        status, login = self.request("POST", "/auth/login", {
            "email": "flow@example.com", "password": "flowpass123",
        }, csrf=False)
        self.assertEqual(status, 200)
        self.csrf = login["data"]["csrfToken"]

        status, ward = self.request("POST", "/wards", {
            "hospitalName": "플로우병원", "wardName": "1병동",
            "requiredStaff": {"D": 2, "E": 2, "N": 1}, "rulePreset": "GENERAL",
        })
        self.assertEqual(status, 201)
        self.assertEqual(ward["data"]["wardName"], "1병동")

        specs = [
            ("김차지", "CHARGE", "ACTIVE"),
            ("이프리셉터", "PRECEPTOR", "ACTIVE"),
            ("박일반", "GENERAL", "ACTIVE"),
            ("최신입", "NEW", "ACTIVE"),
            ("정임신", "GENERAL", "PREGNANT"),
        ]
        for spec in specs:
            status, _ = self.request("POST", "/wards/me/nurses", nurse_payload(*spec))
            self.assertEqual(status, 201)

        status, schedule = self.request("POST", "/wards/me/schedules", {"yearMonth": "2026-10"})
        self.assertEqual(status, 201)
        schedule_id = schedule["data"]["id"]

        status, job = self.request("POST", f"/wards/me/schedules/{schedule_id}/generations", {"fixedCells": []})
        self.assertEqual(status, 202)
        self.assertEqual(job["data"]["status"], "SUCCEEDED")

        status, result = self.request("GET", f"/wards/me/schedules/{schedule_id}")
        self.assertEqual(status, 200)
        self.assertEqual(len(result["data"]["cells"]), 155)
        self.assertFalse(any(v["severity"] == "HARD" for v in result["data"]["violations"]))


if __name__ == "__main__":
    unittest.main()
