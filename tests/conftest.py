import os
import tempfile
import uuid
from datetime import timedelta

# app.persistence.db가 import 시점에 읽으므로 앱보다 먼저 설정한다.
# TEST_DATABASE_URL로 PostgreSQL 등 다른 DB를 지정할 수 있다 (빈 DB여야 함)
os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/test.db")
os.environ["COOKIE_SECURE"] = "false"
os.environ.setdefault("GENERATION_DEFAULT_SECONDS", "2")  # CP-SAT 탐색 시간 (운영 기본 30초)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.business import auth  # noqa: E402
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


@pytest.fixture(autouse=True)
def _reset_signup_limit():
    """테스트 클라이언트는 모두 같은 IP라 가입 제한을 테스트마다 초기화한다"""
    auth._signups.hits.clear()


def user(name="사용자") -> Api:
    return Api()


def err(r, status, code) -> dict:
    """1.3 오류 형식 확인 후 error 객체를 돌려준다"""
    assert r.status_code == status, r.text
    e = r.json()["error"]
    assert e["code"] == code and e["traceId"], e
    return e


def ward_head(d=1, e=1, n=1, preset="STANDARD") -> Api:
    h = Api()
    h.workspace = h.data("POST", "/workspaces", {
        "hospitalName": "한빛", "wardName": "7병동", "headName": "수간호사", "joinedYear": 2000,
        "careerMonths": 312, "affiliationStart": "2025-01-01",
        "requiredStaff": {"D": d, "E": e, "N": n}, "rulePreset": preset}, status=201)
    h.c.headers["Authorization"] = "Bearer " + h.workspace["managementToken"]
    return h


def member(h: Api, name="간호사") -> tuple[Api, str]:
    # 일반 간호사는 계정을 만들지 않는다. 관리자가 프로필만 등록한다.
    nurse = h.data("POST", "/wards/me/nurses", {**NURSE, "name": name})
    return Api(), nurse["id"]


NURSE = {"dutyRole": "GENERAL", "status": "ACTIVE", "joinedYear": 2025, "careerMonths": 12, "skillLevel": 3,
         "affiliationStart": "2025-01-01"}


def add_nurses(h: Api, count: int, prefix="간호사", **fields) -> list[str]:
    return [h.data("POST", "/wards/me/nurses", {**NURSE, "name": f"{prefix}{i:02}", **fields}, status=201)["id"]
            for i in range(count)]


def fill_rotation(h: Api, s: dict) -> dict:
    """
    간호사 5명을 D,D,E,E,O,O,N,N,O,O 순환으로 2일씩 밀어 채운다 → 매일 D·E·N 1명씩, 하드 위반 없음.
    같은 근무 2일 유지·야간 후 OFF 2일·주별 연속 휴무도 지켜 소프트 위반이 생기지 않는다 (30일 달 기준 야간 6회).
    필요 인원 1/1/1 병동에서 자동 생성 없이 확정 가능한 근무표를 만들 때 쓴다
    """
    pattern = ["D", "D", "E", "E", "O", "O", "N", "N", "O", "O"]
    days = sorted({c["date"] for c in s["cells"]})
    off = next(r for r in h.data("GET", "/wards/me/rules") if r["code"] == "OFF_TARGET")
    h.data("PATCH", f"/wards/me/rules/{off['id']}", {"enabled": False})
    holidays = set(s["holidays"])
    from datetime import date
    changes = [{"nurseId": n["id"], "date": d, "dutyCode":
                ("O" if date.fromisoformat(d).weekday() >= 5 or d in holidays else "D")
                if n["role"] == "HEAD_NURSE" else pattern[(2 * (i - 1) + j) % 10]}
               for i, n in enumerate(s["nurses"]) for j, d in enumerate(days)]
    h.data("PATCH", f"/wards/me/schedules/{s['id']}/cells", {"baseVersion": s["version"], "changes": changes})
    return h.data("GET", f"/wards/me/schedules/{s['id']}")


def confirm_schedule(h, s):
    all_violations = []
    page = 0
    while True:
        result = h("GET", f"/wards/me/schedules/{s['id']}/violations?size=100&page={page}").json()
        all_violations += result["data"]
        page += 1
        if page >= result["meta"]["totalPages"]:
            break
    return h.data("POST", f"/wards/me/schedules/{s['id']}/confirm",
                  {"acknowledgedSoftViolationIds": [v["id"] for v in all_violations if v["severity"] == "SOFT"]})
