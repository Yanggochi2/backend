from conftest import NURSE, data, err, request
from app.business.common import today_kst

def test_seed_has_required_rank_and_career_structure(client):
    rows = data(client, "GET", "/wards/me/nurses?size=100")
    assert len(rows) == 28
    assert sum(n["rank"] == "HEAD_NURSE" for n in rows) == 1
    assert sum(n["rank"] == "CHARGE_NURSE" for n in rows) == 2
    assert sum(n["rank"] == "STAFF_NURSE" for n in rows) == 25
    assert any(n["rank"] == "STAFF_NURSE" and today_kst().year - n["hireYear"] == 25 for n in rows)
    assert any(n["rank"] == "CHARGE_NURSE" and today_kst().year - n["hireYear"] == 23 for n in rows)
    assert all(n["rank"] == "STAFF_NURSE" and today_kst().year - n["hireYear"] in (5, 6)
               for n in rows if n["isPreceptor"])

def test_create_update_and_preceptor_validation(client):
    created = data(client, "POST", "/wards/me/nurses", {**NURSE, "name": "추가 간호사"}, 201)
    updated = data(client, "PATCH", f"/wards/me/nurses/{created['id']}", {"skillLevel": 4, "version": created["version"]})
    assert updated["nurse"]["skillLevel"] == 4
    invalid = request(client, "POST", "/wards/me/nurses", {
        **NURSE, "name": "잘못된 프리셉터", "rank": "CHARGE_NURSE",
        "hireYear": today_kst().year - 5, "isPreceptor": True,
    })
    err(invalid, 422, "INVALID_PRECEPTOR")

def test_legacy_frontend_create_update_and_filters_remain_compatible(client):
    created = data(client, "POST", "/wards/me/nurses", {
        "name": "프론트 계약 간호사", "dutyRole": "NIGHT_ONLY", "status": "ACTIVE",
        "joinedAt": f"{today_kst().year - 7}-01-01", "careerMonths": 101,
        "skillLevel": 3, "affiliationStart": "2024-01-01",
    }, 201)
    assert created["rank"] == "STAFF_NURSE"
    assert created["role"] == "NURSE"
    assert created["dutyRole"] == "NIGHT_ONLY"
    assert created["joinedAt"] == f"{today_kst().year - 7}-01-01"
    assert created["careerMonths"] == 101

    updated = data(client, "PATCH", f"/wards/me/nurses/{created['id']}", {
        "dutyRole": "CHARGE", "version": created["version"],
    })
    assert updated["nurse"]["rank"] == "CHARGE_NURSE"
    assert updated["nurse"]["dutyRole"] == "CHARGE"

    heads = data(client, "GET", "/wards/me/nurses?role=HEAD_NURSE&size=100")
    assert len(heads) == 1 and heads[0]["role"] == "HEAD_NURSE"
    charges = data(client, "GET", "/wards/me/nurses?dutyRole=CHARGE&size=100")
    assert all(n["dutyRole"] == "CHARGE" for n in charges)
