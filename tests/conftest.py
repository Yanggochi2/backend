import os
import tempfile
import uuid
from datetime import timedelta

# app.persistence.db가 import 시점에 읽으므로 앱보다 먼저 설정한다.
# TEST_DATABASE_URL로 PostgreSQL 등 다른 DB를 지정할 수 있다 (빈 DB여야 함)
os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/test.db")
os.environ["COOKIE_SECURE"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.business.common import today_kst  # noqa: E402
from app.main import app  # noqa: E402

NEXT = (today_kst().replace(day=1) + timedelta(days=32)).replace(day=1)  # 다음 달 1일 (모두 미래 날짜)
YM = NEXT.strftime("%Y-%m")


@pytest.fixture(scope="session", autouse=True)
def _lifespan():
    with TestClient(app):  # 테이블 생성
        yield


class Api:
    """사용자 한 명의 쿠키 저장소. 상태 변경 요청에 CSRF 헤더를 자동으로 붙인다"""

    def __init__(self, **kw):
        self.c = TestClient(app, **kw)

    def __call__(self, method, path, json=None, headers=None, **kw):
        h = dict(headers or {})
        if (tok := self.c.cookies.get("CSRF_TOKEN")) and "X-CSRF-Token" not in h:
            h["X-CSRF-Token"] = tok
        return self.c.request(method, "/api/v1" + path, json=json, headers=h, **kw)

    def data(self, method, path, json=None, status=None, **kw):
        r = self(method, path, json, **kw)
        assert r.status_code < 300 if status is None else r.status_code == status, r.text
        return r.json()["data"]


def user(name="사용자") -> Api:
    """가입 + 로그인한 사용자. 테스트끼리 겹치지 않게 이메일은 매번 새로"""
    a, email = Api(), f"{uuid.uuid4().hex[:10]}@x.com"
    a.data("POST", "/auth/signup", {"name": name, "email": email, "password": "pass1234", "termsAgreed": True})
    a.data("POST", "/auth/login", {"email": email, "password": "pass1234"})
    return a


def err(r, status, code) -> dict:
    """1.3 오류 형식 확인 후 error 객체를 돌려준다"""
    assert r.status_code == status, r.text
    e = r.json()["error"]
    assert e["code"] == code and e["traceId"], e
    return e


def ward_head(d=1, e=1, n=1, preset="STANDARD") -> Api:
    """병동을 개설한 수간호사"""
    h = user("수간호사")
    h.data("POST", "/wards", {"hospitalName": "한빛", "wardName": "7병동", "requiredStaff": {"D": d, "E": e, "N": n},
                              "rulePreset": preset}, status=201)
    return h


def member(h: Api, name="간호사") -> tuple[Api, str]:
    """h의 병동에 가입 승인된 일반 간호사와 그 간호사 id"""
    n = user(name)
    code = h.data("GET", "/wards/me/join-code")["code"]
    rid = n.data("POST", "/ward-membership-requests", {"joinCode": code})["id"]
    return n, h.data("POST", f"/wards/me/membership-requests/{rid}/approve")["id"]


NURSE = {"dutyRole": "GENERAL", "status": "ACTIVE", "joinedAt": "2025-01-01", "careerMonths": 12, "skillLevel": 3,
         "affiliationStart": "2025-01-01"}


def add_nurses(h: Api, count: int, prefix="간호사", **fields) -> list[str]:
    return [h.data("POST", "/wards/me/nurses", {**NURSE, "name": f"{prefix}{i:02}", **fields}, status=201)["id"]
            for i in range(count)]

