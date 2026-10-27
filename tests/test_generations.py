from conftest import YM, data

def test_generation_uses_parameterized_rules(client):
    schedule = data(client, "POST", "/wards/me/schedules", {"yearMonth": YM}, 201)
    job = data(client, "POST", f"/wards/me/schedules/{schedule['id']}/generations", {"maxSeconds": 5}, 202)
    assert job["status"] in {"SUCCEEDED", "NO_SOLUTION"}
    assert "hardViolationCount" in job
