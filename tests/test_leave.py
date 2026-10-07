"""병동 탈퇴·계정 탈퇴: 행은 퇴사 처리로 남기고, 이후 일정·신청은 자동 정리"""
from conftest import NEXT, YM, Api, add_nurses, err, fill_rotation, member, user, ward_head


def test_leave_ward_cleans_future_and_notifies_head():
    h = ward_head()
    add_nurses(h, 3)
    n, mine = member(h)
    s = fill_rotation(h, h.data("POST", "/wards/me/schedules", {"yearMonth": YM}))
    n.data("POST", "/wards/me/requests", {"type": "PREFERRED_OFF", "targetDates": [NEXT.replace(day=20).isoformat()],
                                          "reasonCode": "PERSONAL"})
    assert n("POST", "/wards/me/membership/leave").status_code == 204
    me = n.data("GET", "/me")
    assert me["membership"] is None and me["ward"] is None
    err(n("GET", "/wards/me"), 404, "WARD_NOT_FOUND")
    # 행은 퇴사 처리로 남는다
    assert all(x["id"] != mine for x in h.data("GET", "/wards/me/nurses?size=100"))
    row = h.data("GET", f"/wards/me/nurses/{mine}")
    assert row["status"] == "RETIRED" and row["affiliationEnd"] and row["hasAccount"] is False
    # 초안에서 근무가 빠지고(version 증가) 신청은 취소
    after = h.data("GET", f"/wards/me/schedules/{s['id']}")
    assert after["version"] == s["version"] + 1 and all(x["id"] != mine for x in after["nurses"])
    assert h("GET", "/wards/me/requests?status=PENDING").json()["meta"]["totalElements"] == 0
    note = h.data("GET", "/me/notifications?type=MEMBER_LEFT")[0]
    assert YM in note["body"] and note["resourceId"] == mine
    # 다시 가입할 수 있다 (새 행)
    code = h.data("GET", "/wards/me/join-code")["code"]
    assert n.data("POST", "/ward-membership-requests", {"joinCode": code})["status"] == "PENDING"


def test_confirmed_schedule_is_not_changed_but_head_is_told():
    h = ward_head()
    add_nurses(h, 3)
    n, mine = member(h)
    s = fill_rotation(h, h.data("POST", "/wards/me/schedules", {"yearMonth": YM}))
    h.data("POST", f"/wards/me/schedules/{s['id']}/confirm", {})
    assert n("POST", "/wards/me/membership/leave").status_code == 204
    assert h.data("GET", f"/wards/me/schedules/{s['id']}")["status"] == "CONFIRMED"
    assert "확정 취소 후 수정" in h.data("GET", "/me/notifications?type=MEMBER_LEFT")[0]["body"]


def test_last_head_cannot_leave():
    h = ward_head()
    err(h("POST", "/wards/me/membership/leave"), 409, "LAST_HEAD_NURSE")
    err(user()("POST", "/wards/me/membership/leave"), 404, "WARD_NOT_FOUND")


def test_delete_account():
    h = ward_head()
    n, mine = member(h)
    email = n.data("GET", "/me")["user"]["email"]
    err(n("DELETE", "/me", {"password": "wrong1234"}), 401, "INVALID_CREDENTIALS")
    assert n("DELETE", "/me", {"password": "pass1234"}).status_code == 204
    err(n("GET", "/me"), 401, "UNAUTHENTICATED")
    err(Api()("POST", "/auth/login", {"email": email, "password": "pass1234"}), 401, "INVALID_CREDENTIALS")
    row = h.data("GET", f"/wards/me/nurses/{mine}")
    assert row["status"] == "RETIRED" and row["name"] == "간호사", "지난 근무표용 이름은 남는다"
    # 같은 이메일로 다시 가입 가능
    Api().data("POST", "/auth/signup", {"name": "새", "email": email, "password": "pass1234", "termsAgreed": True},
               status=201)
    err(h("DELETE", "/me", {"password": "pass1234"}), 409, "LAST_HEAD_NURSE")
