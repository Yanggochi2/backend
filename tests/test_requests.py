"""온라인 신청·승인·마감 기능 종료."""
import pytest
from conftest import Api, YM, err, ward_head


@pytest.mark.parametrize("method,path", [
    ("POST", "/wards/me/requests"), ("GET", "/wards/me/requests"), ("GET", "/wards/me/requests/me"),
    ("POST", "/wards/me/requests/anything/approve"), ("POST", "/wards/me/requests/anything/cancel"),
    ("GET", f"/wards/me/off-request-deadlines/{YM}"), ("PUT", f"/wards/me/off-request-deadlines/{YM}"),
])
def test_request_routes_removed_for_public_and_manager(method, path):
    for client in (Api(), ward_head()):
        err(client(method, path, {} if method != "GET" else None), 404, "RESOURCE_NOT_FOUND")
