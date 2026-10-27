from conftest import YM, data

def test_create_edit_and_validate_flow(client):
    schedule = data(client, "POST", "/wards/me/schedules", {"yearMonth": YM}, 201)
    cell = schedule["cells"][0]
    result = data(client, "PATCH", f"/wards/me/schedules/{schedule['id']}/cells", {
        "baseVersion": schedule["version"],
        "changes": [{"nurseId": cell["nurseId"], "date": cell["date"], "dutyCode": "D"}],
    })
    assert result["version"] > schedule["version"]
    violations = data(client, "GET", f"/wards/me/schedules/{schedule['id']}/violations?size=100")
    assert isinstance(violations, list)
