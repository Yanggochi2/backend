from conftest import data

def test_rule_parameters_can_be_changed(client):
    rule = next(r for r in data(client, "GET", "/wards/me/rules") if r["code"] == "MAX_CONSECUTIVE_WORK")
    changed = data(client, "PATCH", f"/wards/me/rules/{rule['id']}", {
        "parameters": {"max": 4}, "reason": "클라이언트 확정값 반영", "version": rule["version"],
    })
    assert changed["rule"]["parameters"]["max"] == 4

def test_unconfirmed_role_rules_start_disabled(client):
    rules = {r["code"]: r for r in data(client, "GET", "/wards/me/rules")}
    assert not rules["CHARGE_EVERY_SHIFT"]["enabled"]
    assert not rules["PRECEPTOR_MISMATCH"]["enabled"]
