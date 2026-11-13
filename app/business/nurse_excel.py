"""간호사 등록 양식 다운로드와 원자적 일괄 등록."""
import io
import uuid
from datetime import date, datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy.orm import Session

from app.business import members, nurses
from app.business.common import ApiException, today_kst, unprocessable
from app.domain.model import DutyRole, NurseStatus, StaffType

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_ROWS = 500
HEADERS = ("이름", "입사연도", "직종", "직급", "프리셉터", "총 경력(년)", "총 경력(개월)",
           "숙련도", "상태", "소속 시작일")
REQUIRED_HEADERS = HEADERS
ROLE_ALIASES = {
    "일반": DutyRole.GENERAL, "GENERAL": DutyRole.GENERAL,
    "차지": DutyRole.CHARGE, "CHARGE": DutyRole.CHARGE,
    "신규": DutyRole.NEW, "NEW": DutyRole.NEW,
    "나이트전담": DutyRole.NIGHT_ONLY, "NIGHT_ONLY": DutyRole.NIGHT_ONLY,
    "프리셉터": DutyRole.PRECEPTOR, "PRECEPTOR": DutyRole.PRECEPTOR,
}
STATUS_ALIASES = {
    "재직": NurseStatus.ACTIVE, "ACTIVE": NurseStatus.ACTIVE,
    "임신": NurseStatus.PREGNANT, "PREGNANT": NurseStatus.PREGNANT,
    "휴직": NurseStatus.ON_LEAVE, "ON_LEAVE": NurseStatus.ON_LEAVE,
}
STAFF_ALIASES = {
    "간호사": StaffType.NURSE, "NURSE": StaffType.NURSE,
    "간호조무사": StaffType.NURSING_ASSISTANT, "NURSING_ASSISTANT": StaffType.NURSING_ASSISTANT,
}


class RowError(ValueError):
    def __init__(self, field: str, message: str):
        self.field, self.message = field, message


def template(db: Session, user_id: uuid.UUID) -> bytes:
    members.require_head(db, user_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "간호사 등록"
    ws.append(HEADERS)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:J{MAX_ROWS + 1}"
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="4472C4")
    widths = (16, 12, 20, 16, 14, 14, 16, 10, 14, 16)
    for column, width in zip("ABCDEFGHIJ", widths):
        ws.column_dimensions[column].width = width
    for column, values in (("C", '"NURSE,NURSING_ASSISTANT"'),
                           ("D", '"GENERAL,CHARGE,NEW,NIGHT_ONLY"'), ("E", '"Y,N"'),
                           ("H", '"1,2,3,4,5"'), ("I", '"ACTIVE,PREGNANT,ON_LEAVE"')):
        validation = DataValidation(type="list", formula1=values, allow_blank=False)
        ws.add_data_validation(validation)
        validation.add(f"{column}2:{column}{MAX_ROWS + 1}")
    for row in range(2, MAX_ROWS + 2):
        ws.cell(row, 10).number_format = "yyyy-mm-dd"

    guide = wb.create_sheet("작성 안내")
    guide.append(["항목", "입력 방법", "예시"])
    guide_rows = [
        ("이름", "필수, 50자 이하", "홍길동"),
        ("입사연도", f"1900~{today_kst().year}", "2022"),
        ("직종", "NURSE/NURSING_ASSISTANT", "NURSE"),
        ("직급", "GENERAL/CHARGE/NEW/NIGHT_ONLY", "GENERAL"),
        ("프리셉터", "Y 또는 N", "N"),
        ("총 경력(년)", "필수, 0 이상의 정수", "3"),
        ("총 경력(개월)", "필수, 0~11 정수", "6"),
        ("숙련도", "필수, 1~5 정수", "3"),
        ("상태", "ACTIVE/PREGNANT/ON_LEAVE", "ACTIVE"),
        ("소속 시작일", "YYYY-MM-DD", "2026-01-01"),
    ]
    for row in guide_rows:
        guide.append(row)
    for cell in guide[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="4472C4")
    guide.column_dimensions["A"].width = 18
    guide.column_dimensions["B"].width = 42
    guide.column_dimensions["C"].width = 18
    return _save(wb)


def import_nurses(db: Session, user_id: uuid.UUID, data: bytes) -> dict:
    members.require_head(db, user_id)
    wb = _open(data)
    try:
        parsed, errors = _read(wb.worksheets[0])
    finally:
        wb.close()
    if errors:
        raise unprocessable("INVALID_NURSE_EXCEL", "간호사 등록 양식의 값을 확인해 주세요", {"errors": errors})
    if not parsed:
        raise unprocessable("INVALID_NURSE_EXCEL", "등록할 간호사가 없습니다")

    created = []
    for row_number, fields in parsed:
        try:
            created.append(nurses.create(db, user_id, fields))
        except ApiException as error:
            details = {"errors": [{"row": row_number, "field": "행", "reason": error.code,
                                    "message": error.message}]}
            raise unprocessable("INVALID_NURSE_EXCEL", f"{row_number}행을 등록할 수 없습니다", details) from None
    return {"createdCount": len(created), "nurses": created}


def _read(ws) -> tuple[list[tuple[int, dict]], list[dict]]:
    """빈 행·오류 행을 포함해 검사한 모든 데이터 행이 MAX_ROWS를 넘으면 더 읽지 않는다."""
    rows = _rows(ws)
    columns = _columns(next(rows, ()))
    parsed: list[tuple[int, dict]] = []
    errors = []
    for row_number, row in enumerate(rows, start=2):
        if row_number > MAX_ROWS + 1:
            raise unprocessable("INVALID_NURSE_EXCEL", f"최대 {MAX_ROWS}명까지 등록할 수 있습니다")
        values = {header: row[index - 1] if index <= len(row) else None for header, index in columns.items()}
        if not any(value not in (None, "") for value in values.values()):
            continue
        try:
            parsed.append((row_number, _parse(values)))
        except RowError as error:
            errors.append({"row": row_number, "field": error.field, "reason": error.message})
    return parsed, errors


def _rows(ws):
    """read-only 모드는 행을 순회하면서 XML을 읽으므로, 읽기 실패도 양식 오류로 돌려준다."""
    iterator = ws.iter_rows(values_only=True)
    while True:
        try:
            row = next(iterator)
        except StopIteration:
            return
        except Exception:
            raise unprocessable("INVALID_NURSE_EXCEL", "엑셀 형식을 읽을 수 없습니다") from None
        yield row


def _columns(header_row) -> dict[str, int]:
    headers = [str(value).strip() if value is not None else "" for value in header_row]
    present = [header for header in headers if header]
    missing = sorted(set(REQUIRED_HEADERS) - set(present))
    unexpected = sorted(set(present) - set(HEADERS))
    duplicates = sorted({header for header in present if present.count(header) > 1})
    if missing or unexpected or duplicates:
        raise unprocessable("INVALID_NURSE_EXCEL", "간호사 등록 양식의 열을 확인해 주세요",
                            {"missingHeaders": missing, "unexpectedHeaders": unexpected,
                             "duplicateHeaders": duplicates})
    return {header: headers.index(header) + 1 for header in HEADERS if header in headers}


def _parse(values: dict) -> dict:
    for field, value in values.items():
        if isinstance(value, str) and value.startswith("="):
            raise RowError(field, "수식은 입력할 수 없습니다")
    name = _required_text(values["이름"], "이름")
    if len(name) > 50:
        raise RowError("이름", "50자 이하여야 합니다")
    role = _enum(values["직급"], "직급", ROLE_ALIASES)
    staff_type = _enum(values["직종"], "직종", STAFF_ALIASES)
    status = _enum(values["상태"], "상태", STATUS_ALIASES)
    joined_year = _integer(values["입사연도"], "입사연도")
    if not 1900 <= joined_year <= today_kst().year:
        raise RowError("입사연도", f"1900~{today_kst().year} 범위여야 합니다")
    is_preceptor = _boolean(values["프리셉터"], "프리셉터")
    if role == DutyRole.PRECEPTOR:
        role, is_preceptor = DutyRole.GENERAL, True
    career_years = _integer(values["총 경력(년)"], "총 경력(년)")
    remaining_months = _integer(values["총 경력(개월)"], "총 경력(개월)")
    if career_years < 0:
        raise RowError("총 경력(년)", "0 이상이어야 합니다")
    if not 0 <= remaining_months <= 11:
        raise RowError("총 경력(개월)", "0~11 범위여야 합니다")
    career_months = career_years * 12 + remaining_months
    skill_level = _integer(values["숙련도"], "숙련도")
    if not 1 <= skill_level <= 5:
        raise RowError("숙련도", "1~5 범위여야 합니다")
    try:
        career_months, skill_level = nurses.experience_values(joined_year, career_months, skill_level)
    except ApiException as error:
        api_field = error.field_errors[0]["field"] if error.field_errors else None
        field = "숙련도" if api_field == "skillLevel" else "총 경력"
        raise RowError(field, error.message) from None
    start = _date(values["소속 시작일"], "소속 시작일")
    return {"name": name, "staff_type": staff_type, "duty_role": role, "status": status, "joined_year": joined_year,
            "is_preceptor": is_preceptor, "career_months": career_months, "skill_level": skill_level,
            "affiliation_start": start, "affiliation_end": None, "preceptor_of": None}


def _required_text(value, field: str) -> str:
    if value is None or not str(value).strip():
        raise RowError(field, "필수 값입니다")
    return str(value).strip()


def _enum(value, field: str, aliases: dict):
    key = _required_text(value, field).upper().replace(" ", "")
    if key not in aliases:
        raise RowError(field, f"허용 값: {', '.join(dict.fromkeys(str(x) for x in aliases.values()))}")
    return aliases[key]


def _integer(value, field: str) -> int:
    if isinstance(value, bool):
        raise RowError(field, "정수여야 합니다")
    if isinstance(value, int) or isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdecimal():
        return int(value.strip())
    raise RowError(field, "정수여야 합니다")


def _boolean(value, field: str) -> bool:
    if isinstance(value, bool):
        return value
    key = "" if value is None else str(value).strip().upper()
    if key in ("Y", "YES", "TRUE", "1", "예"):
        return True
    if key in ("N", "NO", "FALSE", "0", "아니오", ""):
        return False
    raise RowError(field, "Y 또는 N이어야 합니다")


def _date(value, field: str, optional: bool = False) -> date | None:
    if value in (None, "") and optional:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        raise RowError(field, "YYYY-MM-DD 형식이어야 합니다") from None


def _open(data: bytes):
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    except Exception:
        raise unprocessable("INVALID_NURSE_EXCEL", "엑셀 형식을 읽을 수 없습니다") from None
    if not wb.worksheets:
        wb.close()
        raise unprocessable("INVALID_NURSE_EXCEL", "엑셀 형식을 읽을 수 없습니다")
    # 파일의 dimension 기록을 믿지 않고 실제 행을 읽는다 (행 수 제한은 _read에서 건다)
    wb.worksheets[0].reset_dimensions()
    return wb


def _save(wb: Workbook) -> bytes:
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()
