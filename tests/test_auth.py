from conftest import request

def test_login_and_signup_endpoints_are_removed(client):
    assert request(client, "POST", "/auth/login", {}).status_code == 404
    assert request(client, "POST", "/auth/signup", {}).status_code == 404
    assert request(client, "GET", "/me").status_code == 404
