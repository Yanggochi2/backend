from conftest import data, request

def test_head_cannot_be_retired(client):
    head = next(n for n in data(client, "GET", "/wards/me/nurses?size=100") if n["rank"] == "HEAD_NURSE")
    result = request(client, "POST", f"/wards/me/nurses/{head['id']}/retire", {"affiliationEnd": "2026-12-31"})
    assert result.status_code == 409
