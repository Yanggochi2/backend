import os
import tempfile
from datetime import timedelta

os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/test.db")
os.environ.setdefault("GENERATION_DEFAULT_SECONDS", "2")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.business.common import today_kst  # noqa: E402
from app.main import app  # noqa: E402
from app.persistence.db import Base, SessionLocal, engine  # noqa: E402
from app.seed import seed  # noqa: E402

NEXT = (today_kst().replace(day=1) + timedelta(days=32)).replace(day=1)
YM = NEXT.strftime("%Y-%m")
NURSE = {
    "rank": "STAFF_NURSE", "hireYear": today_kst().year - 3, "isPreceptor": False,
    "status": "ACTIVE", "skillLevel": 3, "affiliationStart": today_kst().replace(month=1, day=1).isoformat(),
}


@pytest.fixture(autouse=True)
def reset_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
        db.commit()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def request(client, method, path, json=None, **kwargs):
    return client.request(method, "/api/v1" + path, json=json, **kwargs)


def data(client, method, path, json=None, status=200, **kwargs):
    response = request(client, method, path, json, **kwargs)
    assert response.status_code == status, response.text
    return response.json()["data"]


def err(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code
    return body
