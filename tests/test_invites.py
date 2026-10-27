from conftest import request

def test_account_invite_api_is_removed(client):
    assert request(client, "POST", "/wards/me/nurses/00000000-0000-0000-0000-000000000000/invite", {}).status_code == 404
