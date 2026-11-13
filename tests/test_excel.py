"""API-SCH-09~12 엑셀 내보내기·가져오기"""
import io

from openpyxl import Workbook, load_workbook

from conftest import NEXT, NURSE, YM, add_nurses, err, fill_rotation, member, ward_head


def _xlsx(wb) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_export_then_import_with_mapping():
    h = ward_head()
    add_nurses(h, 4)
    s = fill_rotation(h, h.data("POST", "/wards/me/schedules", {"yearMonth": YM}))
    url = f"/wards/me/schedules/{s['id']}"
    x = h("GET", url + "/export.xlsx")
    assert x.status_code == 200 and x.headers["content-type"].startswith("application/vnd.openxmlformats")
    wb = load_workbook(io.BytesIO(x.content))
    ws = wb.active
    # 1행 요일, 2행 일자 머리글(이름·남은 OFF·일자·총 OFF), 3행부터 간호사. 일자 1일은 3열
    assert ws.cell(2, 1).value == "이름" and ws.cell(3, 1).value == s["nurses"][0]["name"]
    ws.cell(3, 4).value = "OFF"  # 첫 간호사 2일 → O
    ws.cell(4, 5).value = "???"  # 모르는 코드 → 매핑 필요
    ws.cell(5, 1).value = "없는사람"  # 미등록 이름 → 행 매핑 필요

    err(h("POST", url + "/import-previews", files={"file": ("x.xlsx", b"nope")}), 422, "INVALID_EXCEL_FORMAT")
    p = h.data("POST", url + "/import-previews", files={"file": ("s.xlsx", _xlsx(wb))}, status=202)
    assert p["unresolved"] == {"rows": [5], "codes": ["???"]}
    purl = f"{url}/import-previews/{p['id']}"
    err(h("POST", purl + "/apply", {"baseVersion": s["version"]}), 422, "UNRESOLVED_MAPPING")
    err(h("PATCH", purl, {"dutyMappings": [{"raw": "???", "dutyCode": "AL"}]}), 422, "INVALID_MAPPING")
    p = h.data("PATCH", purl, {"dutyMappings": [{"raw": "???", "dutyCode": "O"}],
                               "nurseMappings": [{"rowNumber": 5, "nurseId": None}]})
    assert p["unresolved"] == {"rows": [], "codes": []}
    err(h("POST", purl + "/apply", {"baseVersion": s["version"] - 1}), 409, "VERSION_CONFLICT")
    r = h.data("POST", purl + "/apply", {"baseVersion": s["version"]})
    first = s["nurses"][0]["id"]
    cell = next(c for c in r["schedule"]["cells"] if c["nurseId"] == first and c["date"] == NEXT.replace(day=2)
                .isoformat())
    assert cell["dutyCode"] == "O" and r["schedule"]["version"] == s["version"] + 1
    err(h("POST", purl + "/apply", {"baseVersion": r["schedule"]["version"]}), 409, "INVALID_RESOURCE_STATE")


def test_export_is_head_only():
    h = ward_head()
    n, _ = member(h)
    s = h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    err(n("GET", f"/wards/me/schedules/{s['id']}/export.xlsx"), 401, "UNAUTHENTICATED")


def test_export_writes_formula_like_names_as_text():
    """이름이 "="로 시작해도 수식이 아니라 텍스트 셀로 내보낸다 (엑셀 수식 주입 방지)"""
    h = ward_head()
    add_nurses(h, 1, prefix="=1+")
    s = h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    ws = load_workbook(io.BytesIO(h("GET", f"/wards/me/schedules/{s['id']}/export.xlsx").content)).active
    c = next(ws.cell(r, 1) for r in range(2, ws.max_row + 1) if str(ws.cell(r, 1).value).startswith("="))
    assert c.data_type == "s" and c.value == "=1+00"


def test_export_layout_matches_the_schedule_screen_and_only_the_schedule():
    h = ward_head()
    add_nurses(h, 4)
    s = fill_rotation(h, h.data("POST", "/wards/me/schedules", {"yearMonth": YM}))
    ws = load_workbook(io.BytesIO(h("GET", f"/wards/me/schedules/{s['id']}/export.xlsx").content)).active
    days = len({c["date"] for c in s["cells"]})
    last_day, total = 2 + days, 3 + days
    # 근무표만: 제목 줄·직급 열·D/E/N/AL 합계 열·범례가 없다
    assert [ws.cell(2, c).value for c in (1, 2, 3, last_day, total)] == ["이름", "남은 OFF", 1, days, "총 OFF"]
    assert ws.max_column == total and "범례" not in [ws.cell(r, 1).value for r in range(1, ws.max_row + 1)]
    assert ws.cell(1, 3).value == "월화수목금토일"[NEXT.replace(day=1).weekday()]  # 위 줄 요일, 아래 줄 날짜
    # 간호사 행: 이름, 남은 OFF = max(목표 − O, 0), 총 OFF = O 개수
    nurses = 5  # 수간호사 + 4
    for line in range(3, 3 + nurses):
        duties = [ws.cell(line, c).value for c in range(3, last_day + 1)]
        assert ws.cell(line, total).value == duties.count("O")
        assert ws.cell(line, 2).value >= 0
    assert ws.cell(3, 1).value == s["nurses"][0]["name"]
    # 근무 칸 색 (화면과 같은 진한 배경 + 흰 글씨, O는 옅은 회색)
    cells = {ws.cell(line, c).value: ws.cell(line, c) for line in range(3, 3 + nurses) for c in range(3, last_day + 1)}
    assert cells["D"].fill.fgColor.rgb.endswith("B97D00") and cells["D"].font.color.rgb.endswith("FFFFFF")
    assert cells["O"].fill.fgColor.rgb.endswith("EEF0F3")
    # 아랫줄: 날짜별 D/E/N 인원 "실제/필요"
    assert [ws.cell(3 + nurses + i, 1).value for i in range(3)] == ["D 인원", "E 인원", "N 인원"]
    assert all(str(ws.cell(3 + nurses, c).value).endswith("/1") for c in range(3, last_day + 1))
    # 고정 틀 · 인쇄 설정
    assert ws.freeze_panes == "C3"
    assert ws.page_setup.orientation == "landscape" and int(ws.page_setup.paperSize) == int(ws.PAPERSIZE_A4)
    assert ws.sheet_properties.pageSetUpPr.fitToPage and ws.page_setup.fitToHeight == 1


def test_import_does_not_treat_a_number_in_title_as_the_date_header():
    h = ward_head()
    add_nurses(h, 1)
    schedule = h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["근무표 제목", 11])
    sheet.append(["이름", 1, 2])
    sheet.append(["간호사00", "D", "E"])
    preview = h.data("POST", f"/wards/me/schedules/{schedule['id']}/import-previews",
                     files={"file": ("schedule.xlsx", _xlsx(workbook))}, status=202)
    assert [row["name"] for row in preview["rows"]] == ["간호사00"]
    assert [(cell["date"][-2:], cell["raw"]) for cell in preview["cells"]] == [("01", "D"), ("02", "E")]


def test_import_still_reads_a_file_exported_in_the_previous_layout():
    """이전 내보내기 양식(제목 줄·직급 열·일자 아래 요일 줄·인원 (필요 n) 행·범례)으로 저장해 둔 파일도 가져온다"""
    h = ward_head()
    add_nurses(h, 2)
    s = h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    days = len({c["date"] for c in s["cells"]})
    wb = Workbook()
    ws = wb.active
    ws.append(["2026년 12월 근무표 · 7병동"])
    ws.append(["이름", "직급"] + list(range(1, days + 1)) + ["D", "E", "N", "OFF", "AL"])
    ws.append([None, None] + ["월"] * days)
    ws.append([s["nurses"][1]["name"], "간호사", "D", "E"])
    ws.append(["D 인원 (필요 1)", None] + [1] * days)
    ws.append(["범례", "D 데이"])
    p = h.data("POST", f"/wards/me/schedules/{s['id']}/import-previews", files={"file": ("old.xlsx", _xlsx(wb))}, status=202)
    assert [r["name"] for r in p["rows"]] == [s["nurses"][1]["name"]]
    assert sorted(c["raw"] for c in p["cells"]) == ["D", "E"] and p["unresolved"] == {"rows": [], "codes": []}


def _export_rows(h, schedule_id) -> dict:
    """내보낸 엑셀의 간호사 행 → {이름: (남은 OFF, 총 OFF)}. 아랫줄 인원 행에서 멈춘다"""
    ws = load_workbook(io.BytesIO(h("GET", f"/wards/me/schedules/{schedule_id}/export.xlsx").content)).active
    rows = {}
    for r in range(3, ws.max_row + 1):
        name = ws.cell(r, 1).value
        if not name or "인원" in str(name):
            break
        rows[name] = (ws.cell(r, 2).value, ws.cell(r, ws.max_column).value)
    return rows


def test_export_follows_the_database_not_fixed_rows():
    """행·근무·OFF 값은 고정값이 아니라 그때그때 DB에서 만든다: 간호사 추가·근무 입력·삭제가 다시 내보낸 파일에 바로 반영된다"""
    h = ward_head()
    add_nurses(h, 3)
    s = h.data("POST", "/wards/me/schedules", {"yearMonth": YM})
    sid = s["id"]
    assert set(_export_rows(h, sid)) == {"수간호사", "간호사00", "간호사01", "간호사02"}

    # 간호사를 등록하면 그 사람 행이 늘어난다 (이름은 DB 값 그대로, 근무가 없으면 총 OFF 0)
    nurse = h.data("POST", "/wards/me/nurses", {**NURSE, "name": "신규등록"}, status=201)
    before = _export_rows(h, sid)
    assert set(before) == {"수간호사", "간호사00", "간호사01", "간호사02", "신규등록"} and before["신규등록"][1] == 0

    # 근무(O)를 입력하면 총 OFF는 늘고 남은 OFF는 줄며, 두 값 모두 API 통계와 같다
    version = h.data("GET", f"/wards/me/schedules/{sid}")["version"]
    h.data("PATCH", f"/wards/me/schedules/{sid}/cells", {"baseVersion": version, "changes": [
        {"nurseId": nurse["id"], "date": f"{YM}-05", "dutyCode": "O"},
        {"nurseId": nurse["id"], "date": f"{YM}-06", "dutyCode": "O"}]})
    after = _export_rows(h, sid)
    assert after["신규등록"] == (before["신규등록"][0] - 2, 2)
    api = h.data("GET", f"/wards/me/schedules/{sid}")
    names = {n["id"]: n["name"] for n in api["nurses"]}
    assert {names[x["nurseId"]]: (x["remainingOff"], x["O"]) for x in api["statistics"]} == after

    # 간호사를 삭제하면 (다음 달 근무표에서) 그 행이 사라지고 나머지는 그대로다
    assert h("DELETE", f"/wards/me/nurses/{nurse['id']}").status_code == 204
    gone = _export_rows(h, sid)
    assert "신규등록" not in gone and gone == {k: v for k, v in before.items() if k != "신규등록"}
