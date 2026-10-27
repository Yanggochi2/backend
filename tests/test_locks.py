from conftest import YM, data, request

def test_edit_lock_lifecycle(client):
    schedule = data(client, "POST", "/wards/me/schedules", {"yearMonth": YM}, 201)
    lock = data(client, "POST", f"/wards/me/schedules/{schedule['id']}/lock", {}, 201)
    response = request(client, "DELETE", f"/wards/me/schedules/{schedule['id']}/lock",
                       headers={"X-Schedule-Lock-Token": lock["lockToken"]})
    assert response.status_code == 204
