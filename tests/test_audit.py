from conftest import NURSE, data

def test_nurse_change_is_audited(client):
    nurse = data(client, "POST", "/wards/me/nurses", {**NURSE, "name": "감사 대상"}, 201)
    logs = data(client, "GET", "/wards/me/audit-logs?actionType=NURSE_REGISTERED")
    assert any(row["targetId"] == nurse["id"] for row in logs)
