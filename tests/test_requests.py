from conftest import request

def test_online_work_request_api_is_removed(client):
    assert request(client, "GET", "/wards/me/requests").status_code == 404
    assert request(client, "POST", "/wards/me/requests", {}).status_code == 404
    assert request(client, "GET", "/wards/me/off-request-deadlines").status_code == 404
