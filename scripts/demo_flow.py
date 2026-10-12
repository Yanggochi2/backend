#!/usr/bin/env python3
"""회원가입부터 자동 근무표 결과 조회까지 전체 API 흐름을 실행한다."""

from __future__ import annotations

import json
import sys
import time
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener


BASE_URL = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
API_URL = f"{BASE_URL}/api/v1"


class ApiClient:
    def __init__(self) -> None:
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.csrf_token: str | None = None

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if self.csrf_token and method not in {"GET", "HEAD", "OPTIONS"}:
            headers["X-CSRF-Token"] = self.csrf_token

        request = Request(f"{API_URL}{path}", data=data, headers=headers, method=method)
        try:
            with self.opener.open(request) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return payload["data"]
        except HTTPError as error:
            detail = error.read().decode("utf-8")
            raise RuntimeError(f"{method} {path} 실패 ({error.code}): {detail}") from error


def main() -> None:
    client = ApiClient()
    unique = int(time.time() * 1000)
    email = f"flow-{unique}@example.com"

    print("1/7 회원가입")
    client.request(
        "POST",
        "/auth/signup",
        {
            "name": "근무표 관리자",
            "email": email,
            "password": "FlowDemo!2026",
            "termsAgreed": True,
        },
    )

    print("2/7 로그인")
    login = client.request(
        "POST", "/auth/login", {"email": email, "password": "FlowDemo!2026"}
    )
    client.csrf_token = login["csrfToken"]

    print("3/7 병동 개설")
    ward = client.request(
        "POST",
        "/wards",
        {
            "hospitalName": "통합테스트병원",
            "wardName": f"통합병동-{unique}",
            "requiredStaff": {"D": 2, "E": 2, "N": 1},
            "rulePreset": "GENERAL",
        },
    )

    print("4/7 간호사 5명 추가")
    nurses = [
        ("김차지", "CHARGE", 5, 96),
        ("이선임", "GENERAL", 4, 72),
        ("박간호", "GENERAL", 3, 48),
        ("최간호", "GENERAL", 3, 36),
        ("정간호", "GENERAL", 3, 24),
    ]
    for name, duty_role, skill_level, career_months in nurses:
        client.request(
            "POST",
            "/wards/me/nurses",
            {
                "name": name,
                "dutyRole": duty_role,
                "status": "ACTIVE",
                "joinedAt": "2024-01-01",
                "careerMonths": career_months,
                "skillLevel": skill_level,
                "affiliationStart": "2024-01-01",
                "affiliationEnd": None,
                "preceptorOf": [],
            },
        )

    print("5/7 월 근무표 생성")
    schedule = client.request("POST", "/wards/me/schedules", {"yearMonth": "2026-10"})

    print("6/7 자동 편성 실행")
    generation = client.request(
        "POST",
        f"/wards/me/schedules/{schedule['id']}/generations",
        {"fixedCells": []},
    )

    print("7/7 결과 조회")
    result = client.request("GET", f"/wards/me/schedules/{schedule['id']}")
    hard_violations = [item for item in result["violations"] if item["severity"] == "HARD"]

    summary = {
        "wardId": ward["id"],
        "scheduleId": schedule["id"],
        "generationStatus": generation["status"],
        "cellCount": len(result["cells"]),
        "hardViolationCount": len(hard_violations),
    }
    print("\n완료")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, URLError) as error:
        print(f"오류: {error}", file=sys.stderr)
        raise SystemExit(1)
