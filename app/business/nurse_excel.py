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
from app.domain.model import DutyRole, NurseStatus

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_ROWS = 500
HEADERS = ("이름", "직무역할", "상태", "입사연도", "프리셉터", "경력개월", "숙련도", "소속시작일", "소속종료일")
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
    ws.auto_filter.ref = f"A1:I{MAX_ROWS + 1}"
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="4472C4")
    widths = (16, 16, 14, 12, 14, 12, 10, 16, 16)
    for column, width in zip("ABCDEFGHI", widths):
        ws.column_dimensions[column].width = width
    for column, values in (("B", '"GENERAL,CHARGE,NEW,NIGHT_ONLY"'),
                           ("C", '"ACTIVE,PREGNANT,ON_LEAVE"'), ("E", '"Y,N"')):
        validation = DataValidation(type="list", formula1=values, allow_blank=False)
        ws.add_data_validation(validation)
        validation.add(f"{column}2:{column}{MAX_ROWS + 1}")
    for row in range(2, MAX_ROWS + 2):
        ws.cell(row, 8).number_format = ws.cell(row, 9).number_format = "yyyy-mm-dd"

    guide = wb.create_sheet("작성 안내")
    guide.append(["항목", "입력 방법", "예시"])
    guide_rows = [
        ("이름", "필수, 50자 이하", "홍길동"),
        ("직무역할", "GENERAL/CHARGE/NEW/NIGHT_ONLY", "GENERAL"),
        ("상태", "ACTIVE/PREGNANT/ON_LEAVE", "ACTIVE"),
        ("입사연도", f"1900~{today_kst().year}", "2022"),
        ("프리셉터", "Y 또는 N", "N"),
        ("경력개월", "0 이상의 정수", "36"),
        ("숙련도", "1~5 정수", "3"),
        ("소속시작일", "YYYY-MM-DD", "2026-01-01"),
        ("소속종료일", "선택, YYYY-MM-DD", ""),
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
    ws = _open(data)
    columns = _columns(ws)
    parsed: list[tuple[int, dict]] = []
    errors = []
    for row in ws.iter_rows(min_row=2):
        values = {header: row[index - 1].value for header, index in columns.items()}
        if not any(value not in (None, "") for value in values.values()):
            continue
        if len(parsed) >= MAX_ROWS:
            raise unprocessable("INVALID_NURSE_EXCEL", f"최대 {MAX_ROWS}명까지 등록할 수 있습니다")
        try:
            parsed.append((row[0].row, _parse(values)))
        except RowError as error:
            errors.append({"row": row[0].row, "field": error.field, "reason": error.message})
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


def _columns(ws) -> dict[str, int]:
    headers = [str(cell.value).strip() if cell.value is not None else "" for cell in ws[1]]
    present = [header for header in headers if header]
    missing = sorted(set(HEADERS) - set(present))
    unexpected = sorted(set(present) - set(HEADERS))
    duplicates = sorted({header for header in present if present.count(header) > 1})
    if missing or unexpected or duplicates:
        raise unprocessable("INVALID_NURSE_EXCEL", "간호사 등록 양식의 열을 확인해 주세요",
                            {"missingHeaders": missing, "unexpectedHeaders": unexpected,
                             "duplicateHeaders": duplicates})
    return {header: headers.index(header) + 1 for header in HEADERS}


def _parse(values: dict) -> dict:
    for field, value in values.items():
        if isinstance(value, str) and value.startswith("="):
            raise RowError(field, "수식은 입력할 수 없습니다")
    name = _required_text(values["이름"], "이름")
    if len(name) > 50:
        raise RowError("이름", "50자 이하여야 합니다")
    role = _enum(values["직무역할"], "직무역할", ROLE_ALIASES)
    status = _enum(values["상태"], "상태", STATUS_ALIASES)
    joined_year = _integer(values["입사연도"], "입사연도")
    if not 1900 <= joined_year <= today_kst().year:
        raise RowError("입사연도", f"1900~{today_kst().year} 범위여야 합니다")
    is_preceptor = _boolean(values["프리셉터"], "프리셉터")
    if role == DutyRole.PRECEPTOR:
        role, is_preceptor = DutyRole.GENERAL, True
    career_months = _integer(values["경력개월"], "경력개월")
    if career_months < 0:
        raise RowError("경력개월", "0 이상이어야 합니다")
    skill_level = _integer(values["숙련도"], "숙련도")
    if not 1 <= skill_level <= 5:
        raise RowError("숙련도", "1~5 범위여야 합니다")
    start = _date(values["소속시작일"], "소속시작일")
    end = _date(values["소속종료일"], "소속종료일", optional=True)
    if end is not None and end < start:
        raise RowError("소속종료일", "소속시작일보다 빠를 수 없습니다")
    return {"name": name, "duty_role": role, "status": status, "joined_year": joined_year,
            "is_preceptor": is_preceptor, "career_months": career_months, "skill_level": skill_level,
            "affiliation_start": start, "affiliation_end": end, "preceptor_of": None}


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
    if isinstance(value, str) and value.strip().isdigit():
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
        return load_workbook(io.BytesIO(data), data_only=False).worksheets[0]
    except Exception:
        raise unprocessable("INVALID_NURSE_EXCEL", "엑셀 형식을 읽을 수 없습니다") from None


def _save(wb: Workbook) -> bytes:
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()
