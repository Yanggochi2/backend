from conftest import request

def test_personal_notification_api_is_removed(client):
    assert request(client, "GET", "/me/notifications").status_code == 404
