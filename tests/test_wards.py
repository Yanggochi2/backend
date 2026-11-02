from conftest import add_nurses, err, user, ward_head


def test_create_workspace_and_registered_head():
    h = ward_head(2, 2, 1)
    assert h.data("GET", "/wards/me")["requiredStaff"] == {"D": 2, "E": 2, "N": 1}
    head = h.data("GET", "/wards/me/nurses")[0]
    assert head["role"] == "HEAD_NURSE" and head["joinedYear"] == 2000
    assert "joinCode" not in h.workspace


def test_invalid_staffing_rolls_back():
    err(user()("POST", "/workspaces", {"hospitalName": "병원", "wardName": "병동", "headName": "수간",
        "joinedYear": 2000, "affiliationStart": "2025-01-01", "careerMonths": 100,
        "requiredStaff": {"D": 0, "E": 0, "N": 0}}), 422, "INVALID_STAFFING")


def test_other_ward_ids_are_hidden():
    h, other = ward_head(), ward_head()
    nid = add_nurses(h, 1)[0]
    err(other("GET", f"/wards/me/nurses/{nid}"), 404, "NURSE_NOT_FOUND")
