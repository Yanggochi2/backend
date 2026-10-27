from conftest import YM, data, request

def test_schedule_can_be_exported(client):
    schedule = data(client, "POST", "/wards/me/schedules", {"yearMonth": YM}, 201)
    response = request(client, "GET", f"/wards/me/schedules/{schedule['id']}/export.xlsx")
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
