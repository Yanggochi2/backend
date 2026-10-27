from conftest import data, request

def test_single_ward_is_available_without_login(client):
    ward = data(client, "GET", "/wards/me")
    assert ward["hospitalName"] and ward["wardName"]
    assert request(client, "POST", "/wards", {}).status_code == 404
    assert request(client, "POST", "/ward-membership-requests", {}).status_code == 404
