"""간호사 엑셀 양식 다운로드·일괄 등록."""
import io

from openpyxl import load_workbook

from conftest import Api, err, ward_head


def _template(h):
    response = h("GET", "/wards/me/nurses/import-template.xlsx")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
    return load_workbook(io.BytesIO(response.content))


def _bytes(workbook) -> bytes:
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_download_template_and_import_nurses_atomically():
    h = ward_head()
    workbook = _template(h)
    sheet = workbook["간호사 등록"]
    assert [cell.value for cell in sheet[1]] == [
        "이름", "직무역할", "상태", "입사연도", "프리셉터", "경력개월", "숙련도", "소속시작일", "소속종료일"]
    sheet.append(["김차지", "CHARGE", "ACTIVE", 2020, "N", 72, 5, "2025-01-01", None])
    sheet.append(["이프리", "GENERAL", "ACTIVE", 2021, "Y", 60, 4, "2025-01-01", None])

    result = h.data("POST", "/wards/me/nurses/import.xlsx",
                    files={"file": ("nurses.xlsx", _bytes(workbook))}, status=201)
    assert result["createdCount"] == 2
    created = {n["name"]: n for n in result["nurses"]}
    assert created["김차지"]["dutyRole"] == "CHARGE"
    assert created["이프리"]["dutyRole"] == "GENERAL" and created["이프리"]["isPreceptor"]


def test_invalid_import_rolls_back_all_rows():
    h = ward_head()
    workbook = _template(h)
    sheet = workbook["간호사 등록"]
    sheet.append(["등록되면안됨", "GENERAL", "ACTIVE", 2020, "N", 12, 3, "2025-01-01", None])
    sheet.append(["잘못된행", "GENERAL", "ACTIVE", 2020, "N", 12, 9, "2025-01-01", None])
    response = h("POST", "/wards/me/nurses/import.xlsx", files={"file": ("nurses.xlsx", _bytes(workbook))})
    details = err(response, 422, "INVALID_NURSE_EXCEL")["details"]
    assert details["errors"][0]["field"] == "숙련도"
    assert not h.data("GET", "/wards/me/nurses?q=등록되면안됨")


def test_nurse_excel_is_head_only_and_rejects_invalid_file():
    err(Api()("GET", "/wards/me/nurses/import-template.xlsx"), 401, "UNAUTHENTICATED")
    h = ward_head()
    err(h("POST", "/wards/me/nurses/import.xlsx", files={"file": ("bad.xlsx", b"not xlsx")}),
        422, "INVALID_NURSE_EXCEL")
