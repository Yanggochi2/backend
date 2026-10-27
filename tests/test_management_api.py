from conftest import data

def test_charge_endpoint_reflects_rank(client):
    charges = data(client, "GET", "/wards/me/charges")
    assert len(charges) == 2
    assert all(n["rank"] == "CHARGE_NURSE" for n in charges)

def test_rule_catalog_exposes_parameter_contract(client):
    catalog = data(client, "GET", "/wards/me/rule-catalog")
    assert any(r["code"] == "MAX_CONSECUTIVE_WORK" for r in catalog)
