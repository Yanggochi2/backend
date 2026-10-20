"""규칙 설정 → 자동 생성 → 수동 수정 → 확정 → 근무표 확인 시나리오"""
from datetime import date, timedelta

from conftest import NEXT, YM, add_nurses, err, member, ward_head

from app.domain import generator, validator
from app.domain.model import Duty, DutyRole, NurseInfo, NurseStatus, Roster, Rules, Severity


def test_rule_generate_edit_confirm_view():
    h = ward_head(1, 1, 1, preset="MINIMAL")
    n, me = member(h, "일반")
    add_nurses(h, 6)
    off = add_nurses(h, 1, prefix="휴직", status="ON_LEAVE")[0]
    wish = NEXT.replace(day=10).isoformat()
    rid = n.data("POST", "/wards/me/requests", {"type": "PREFERRED_OFF", "targetDates": [wish],
                                                 "reasonCode": "PERSONAL"}, status=201)["id"]
    h.data("POST", f"/wards/me/requests/{rid}/approve")

    # 1. 규칙: MINIMAL에서 꺼진 N→D 금지를 켜고, 연속 근무 최대를 4로
    rs = {r["code"]: r for r in h.data("GET", "/wards/me/rules")}
    assert not rs["NIGHT_TO_DAY"]["enabled"]
    h.data("PATCH", f"/wards/me/rules/{rs['NIGHT_TO_DAY']['id']}", {"enabled": True})
    h.data("PATCH", f"/wards/me/rules/{rs['MAX_CONSECUTIVE_WORK']['id']}", {"enabled": True, "parameters": {"max": 4}})

    # 2. 자동 생성 (편집 잠금 토큰 필요)
    s = h.data("POST", "/wards/me/schedules", {"yearMonth": YM}, status=201)
    lock = {"X-Schedule-Lock-Token": h.data("POST", f"/wards/me/schedules/{s['id']}/lock", status=201)["lockToken"]}
    err(h("POST", f"/wards/me/schedules/{s['id']}/generations", {}), 423, "SCHEDULE_LOCKED")
    job = h.data("POST", f"/wards/me/schedules/{s['id']}/generations", {}, status=202, headers=lock)
    assert job["status"] == "SUCCEEDED" and job["hardViolationCount"] == 0
    s = h.data("GET", f"/wards/me/schedules/{s['id']}")
    assert s["readiness"]["confirmable"]
    grid = {(c["nurseId"], c["date"]): c["dutyCode"] for c in s["cells"]}
    days = sorted({c["date"] for c in s["cells"]})
    assert all(grid[(off, d)] == "O" for d in days), "휴직자는 배정하지 않는다"
    assert grid[(me, wish)] == "O", "희망 오프 반영"
    for nid in {c["nurseId"] for c in s["cells"]}:
        row = [grid[(nid, d)] for d in days]
        assert not any(a == "N" and b == "D" for a, b in zip(row, row[1:])), "켠 규칙이 생성에 반영"
        assert "DDDDD" not in "".join("D" if x in ("D", "E", "N") else "." for x in row), "바꾼 최대값이 생성에 반영"

    # 3. 수동 수정: 연속 5일 근무 → 하드 위반, 확정 불가
    url = f"/wards/me/schedules/{s['id']}/cells"
    first5 = [{"nurseId": me, "date": d, "dutyCode": "D"} for d in days[:5]]
    e = h.data("PATCH", url, {"baseVersion": s["version"], "changes": first5}, headers=lock)
    assert any(v["ruleCode"] == "MAX_CONSECUTIVE_WORK" and v["severity"] == "HARD" for v in e["violations"])
    assert not e["readiness"]["confirmable"] and e["readiness"]["hardViolations"] > 0
    assert err(h("POST", f"/wards/me/schedules/{s['id']}/confirm", {}), 409, "HARD_VIOLATIONS_EXIST")["details"]

    # 4. 앞 4일만 고정하고 재생성 → 고정 셀 유지, 하드 위반 0
    fixed = [{"nurseId": me, "date": d} for d in days[:4]]
    job = h.data("POST", f"/wards/me/schedules/{s['id']}/generations", {"fixedCells": fixed}, status=202, headers=lock)
    assert job["status"] == "SUCCEEDED", job["conflicts"]
    s = h.data("GET", f"/wards/me/schedules/{s['id']}")
    grid = {(c["nurseId"], c["date"]): c["dutyCode"] for c in s["cells"]}
    assert [grid[(me, d)] for d in days[:5]] == ["D"] * 4 + ["O"]

    # 5. 희망 오프 날을 교육(ED)으로 수정 → 소프트 위반만 남음. 확인해야 확정된다
    e = h.data("PATCH", url, {"baseVersion": s["version"], "changes": [{"nurseId": me, "date": wish, "dutyCode": "ED"}]},
               headers=lock)
    soft = [v for v in e["violations"] if v["severity"] == "SOFT"]
    assert e["readiness"]["confirmable"] and any(v["ruleCode"] == "WISH_OFF_IGNORED" for v in soft)
    err(h("POST", f"/wards/me/schedules/{s['id']}/confirm", {}), 409, "SOFT_VIOLATIONS_UNACKNOWLEDGED")
    done = h.data("POST", f"/wards/me/schedules/{s['id']}/confirm",
                  {"acknowledgedSoftViolationIds": [v["id"] for v in soft]})
    assert done["status"] == "CONFIRMED" and done["lock"] is None

    # 6. 일반 간호사가 확정본 확인: 본인 통계만, 수간호사 전용 필드 없음
    v = n.data("GET", f"/wards/me/schedules?yearMonth={YM}")
    assert v["status"] == "CONFIRMED" and v["readiness"] is None and v["lock"] is None
    assert [x["nurseId"] for x in v["statistics"]] == [me]
    assert {(c["nurseId"], c["date"]): c["dutyCode"] for c in v["cells"]}[(me, days[0])] == "D"


def _nurses(k, **kw):
    return [NurseInfo(i, f"n{i}", kw.get("role", DutyRole.GENERAL), NurseStatus.ACTIVE, date(2025, 1, 1))
            for i in range(k)]


def _hard(r: Roster, cells) -> list[str]:
    return [v.rule_id for v in validator.validate(r.with_cells(cells)) if v.severity == Severity.HARD]


def test_generator_looks_ahead_of_fixed_cells():
    m = date(2026, 11, 1)
    fixed = {0: {m + timedelta(days=i): Duty.D for i in range(1, 6)}}  # 2~6일 D 고정 (최대 5일)
    r = Roster(m, _nurses(6), fixed, Rules(1, 1, 1, 3, 5, True), 0)
    cells = generator.generate(r)
    assert _hard(r, cells) == [] and cells[0][m] == Duty.O and cells[0][m + timedelta(days=6)] == Duty.O


def test_generator_never_puts_new_nurse_alone_on_night():
    m = date(2026, 11, 1)
    # 선배 1명은 연속 야간 3일 뒤 N에 못 들어간다 → 신입을 혼자 넣지 말고 인원 부족으로 남긴다
    ns = _nurses(1) + [NurseInfo(i, f"new{i}", DutyRole.NEW, NurseStatus.ACTIVE, date(2025, 1, 1)) for i in (1, 2, 3)]
    r = Roster(m, ns, {}, Rules(0, 0, 1, 3, 5, True), 0)
    hard = _hard(r, generator.generate(r))
    assert "NEW_NIGHT_ALONE" not in hard and "COVERAGE" in hard
