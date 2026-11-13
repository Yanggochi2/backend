"""API-SCH-09~12 엑셀 내보내기·가져오기"""
import io

from openpyxl import Workbook, load_workbook

from conftest import NEXT, YM, add_nurses, err, fill_rotation, member, ward_head


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
    # 1행 제목, 2~3행 일자·요일 머리글, 4행부터 간호사. 일자 1일은 3열
    assert ws.cell(2, 1).value == "이름" and ws.cell(4, 1).value == s["nurses"][0]["name"]
    ws.cell(4, 4).value = "OFF"  # 첫 간호사 2일 → O
    ws.cell(5, 5).value = "???"  # 모르는 코드 → 매핑 필요
    ws.cell(6, 1).value = "없는사람"  # 미등록 이름 → 행 매핑 필요

    err(h("POST", url + "/import-previews", files={"file": ("x.xlsx", b"nope")}), 422, "INVALID_EXCEL_FORMAT")
    p = h.data("POST", url + "/import-previews", files={"file": ("s.xlsx", _xlsx(wb))}, status=202)
    assert p["unresolved"] == {"rows": [6], "codes": ["???"]}
    purl = f"{url}/import-previews/{p['id']}"
    err(h("POST", purl + "/apply", {"baseVersion": s["version"]}), 422, "UNRESOLVED_MAPPING")
    err(h("PATCH", purl, {"dutyMappings": [{"raw": "???", "dutyCode": "AL"}]}), 422, "INVALID_MAPPING")
    p = h.data("PATCH", purl, {"dutyMappings": [{"raw": "???", "dutyCode": "O"}],
                               "nurseMappings": [{"rowNumber": 6, "nurseId": None}]})
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


def test_export_layout_is_a_printable_schedule():
    h = ward_head()
    add_nurses(h, 4)
    s = fill_rotation(h, h.data("POST", "/wards/me/schedules", {"yearMonth": YM}))
    ws = load_workbook(io.BytesIO(h("GET", f"/wards/me/schedules/{s['id']}/export.xlsx").content)).active
    days = len({c["date"] for c in s["cells"]})
    last_day = 2 + days
    # 제목 · 머리글(이름·직급·일자·요일·합계)
    assert ws.cell(1, 1).value == f"{NEXT.year}년 {NEXT.month}월 근무표 · 7병동"
    assert [ws.cell(2, c).value for c in (1, 2, 3, last_day)] == ["이름", "직급", 1, days]
    assert [ws.cell(2, last_day + i).value for i in range(1, 6)] == ["D", "E", "N", "OFF", "AL"]
    assert ws.cell(3, 3).value == "월화수목금토일"[NEXT.replace(day=1).weekday()]
    # 직급 열과 합계 열
    assert ws.cell(4, 2).value == "수간호사" and ws.cell(5, 2).value == "간호사"
    for line in range(4, 4 + 5):
        duties = [ws.cell(line, c).value for c in range(3, last_day + 1)]
        assert [ws.cell(line, last_day + 1 + i).value for i in range(5)] == [duties.count(x) for x in "DENO"] + [0]
    # 근무 칸 색 (화면과 같은 진한 배경 + 흰 글씨, O는 옅은 회색)
    cells = {ws.cell(line, c).value: ws.cell(line, c) for line in range(4, 9) for c in range(3, last_day + 1)}
    assert cells["D"].fill.fgColor.rgb.endswith("B97D00") and cells["D"].font.color.rgb.endswith("FFFFFF")
    assert cells["O"].fill.fgColor.rgb.endswith("EEF0F3")
    # 날짜별 인원 행 · 범례 · 고정 틀 · 인쇄 설정
    assert [ws.cell(9 + i, 1).value for i in range(3)] == ["D 인원 (필요 1)", "E 인원 (필요 1)", "N 인원 (필요 1)"]
    assert ws.cell(12 + 1, 1).value == "범례"
    assert ws.freeze_panes == "C4"
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
