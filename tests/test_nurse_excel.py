"""간호사 엑셀 양식 다운로드·일괄 등록."""
import io
from pathlib import Path

from openpyxl import load_workbook

from app.business.common import today_kst
from conftest import Api, err, ward_head


def _template(h):
    response = h("GET", "/wards/me/nurses/import-template.xlsx")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
    return load_workbook(io.BytesIO(response.content))


def _put(sheet, row: int, values) -> None:
    """양식에 미리 서식이 잡힌 2~501행에 사용자가 입력하듯 값을 쓴다"""
    for column, value in enumerate(values, start=1):
        sheet.cell(row, column, value)


def _bytes(workbook) -> bytes:
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_download_template_and_import_nurses_atomically():
    h = ward_head()
    year = today_kst().year
    workbook = _template(h)
    sheet = workbook["간호사 등록"]
    assert [cell.value for cell in sheet[1]] == [
        "이름", "직무역할", "상태", "입사연도", "프리셉터", "경력개월", "숙련도", "소속시작일", "소속종료일", "직종"]
    _put(sheet, 2, ["김차지", "CHARGE", "ACTIVE", year - 6, "N", 72, 5, "2025-01-01", None, "NURSE"])
    _put(sheet, 3, ["이프리", "GENERAL", "ACTIVE", year - 5, "Y", 60, 4, "2025-01-01", None, "NURSE"])
    _put(sheet, 4, ["박조무", "GENERAL", "ACTIVE", year - 1, "N", 12, None, "2025-01-01", None,
                    "NURSING_ASSISTANT"])

    result = h.data("POST", "/wards/me/nurses/import.xlsx",
                    files={"file": ("nurses.xlsx", _bytes(workbook))}, status=201)
    assert result["createdCount"] == 3
    created = {n["name"]: n for n in result["nurses"]}
    assert created["김차지"]["dutyRole"] == "CHARGE"
    assert created["이프리"]["dutyRole"] == "GENERAL" and created["이프리"]["isPreceptor"]
    assert created["박조무"]["staffType"] == "NURSING_ASSISTANT" and created["박조무"]["skillLevel"] == 3


def test_invalid_import_rolls_back_all_rows():
    h = ward_head()
    year = today_kst().year - 6
    workbook = _template(h)
    sheet = workbook["간호사 등록"]
    _put(sheet, 2, ["등록되면안됨", "GENERAL", "ACTIVE", year, "N", 72, 3, "2025-01-01", None])
    _put(sheet, 3, ["잘못된행", "GENERAL", "ACTIVE", year, "N", 72, 9, "2025-01-01", None])
    response = h("POST", "/wards/me/nurses/import.xlsx", files={"file": ("nurses.xlsx", _bytes(workbook))})
    details = err(response, 422, "INVALID_NURSE_EXCEL")["details"]
    assert details["errors"][0]["field"] == "숙련도"
    assert not h.data("GET", "/wards/me/nurses?q=등록되면안됨")


def test_nurse_excel_is_head_only_and_rejects_invalid_file():
    err(Api()("GET", "/wards/me/nurses/import-template.xlsx"), 401, "UNAUTHENTICATED")
    h = ward_head()
    err(h("POST", "/wards/me/nurses/import.xlsx", files={"file": ("bad.xlsx", b"not xlsx")}),
        422, "INVALID_NURSE_EXCEL")


def test_non_ascii_digit_text_is_row_error():
    h = ward_head()
    workbook = _template(h)
    _put(workbook["간호사 등록"], 2, ["윗첨자", "GENERAL", "ACTIVE", 2020, "N", "²", 3, "2025-01-01", None])
    response = h("POST", "/wards/me/nurses/import.xlsx", files={"file": ("nurses.xlsx", _bytes(workbook))})
    details = err(response, 422, "INVALID_NURSE_EXCEL")["details"]
    assert details["errors"] == [{"row": 2, "field": "경력개월", "reason": "정수여야 합니다"}]


def test_career_months_is_required_like_single_nurse_creation():
    h = ward_head()
    workbook = _template(h)
    year = today_kst().year
    _put(workbook["간호사 등록"], 2,
         ["경력누락", "NEW", "ACTIVE", year, "N", None, None, f"{year}-01-01", None, "NURSE"])
    response = h("POST", "/wards/me/nurses/import.xlsx", files={"file": ("nurses.xlsx", _bytes(workbook))})
    details = err(response, 422, "INVALID_NURSE_EXCEL")["details"]
    assert details["errors"] == [{"row": 2, "field": "경력개월", "reason": "정수여야 합니다"}]


def test_row_limit_counts_blank_rows():
    h = ward_head()
    year = today_kst().year - 6
    workbook = _template(h)
    sheet = workbook["간호사 등록"]
    _put(sheet, 2, ["앞행", "GENERAL", "ACTIVE", year, "N", 72, 3, "2025-01-01", None])
    _put(sheet, 3 + 500, ["멀리", "GENERAL", "ACTIVE", year, "N", 72, 3, "2025-01-01", None])
    response = h("POST", "/wards/me/nurses/import.xlsx", files={"file": ("nurses.xlsx", _bytes(workbook))})
    err(response, 422, "INVALID_NURSE_EXCEL")
    assert not h.data("GET", "/wards/me/nurses?q=앞행")


def test_committed_sample_workbook_can_be_uploaded():
    h = ward_head()
    sample = Path(__file__).parents[1] / "docs" / "samples" / "nurse-import-test.xlsx"
    result = h.data("POST", "/wards/me/nurses/import.xlsx",
                    files={"file": (sample.name, sample.read_bytes())}, status=201)
    assert result["createdCount"] == 4
    assert {row["staffType"] for row in result["nurses"]} == {"NURSE", "NURSING_ASSISTANT"}
    created = {row["name"]: row for row in result["nurses"]}
    assert {name: row["careerMonths"] for name, row in created.items()} == {
        "김차지": 72, "이프리": 60, "박신규": 3, "최조무": 12,
    }
    assert created["박신규"]["skillLevel"] == 1
