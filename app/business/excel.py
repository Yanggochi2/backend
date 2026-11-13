"""SCH-09 엑셀 내보내기 · SCH-10 엑셀 가져오기 (미리보기 → 매핑 수정 → 반영)"""
import io
import re
import uuid
from datetime import date

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties
from sqlalchemy.orm import Session

from app.business import audit, members, schedules
from app.business.common import ApiException, not_found, unprocessable
from app.domain.model import Duty, DutyRole, Role, StaffType
from app.persistence.db import now
from app.persistence.models import ImportPreview, Ward

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# 화면(근무표 시트)과 같은 색: 근무 D·E·N·AL은 진한 배경 + 흰 글씨, O·ED는 옅은 회색
SOLID = {Duty.D: "B97D00", Duty.E: "D9501F", Duty.N: "4F46E5", Duty.AL: "1E7A4C"}
OFF_FILL, OFF_INK = "EEF0F3", "5B6470"
HEAD_FILL, LINE = "F6F7F9", "D0D5DB"
SAT = ("E8F3FF", "3182F6")  # (배경, 글씨) 토요일
SUN = ("FFEBEE", "F04452")  # 일요일·공휴일
WEEKDAYS = "월화수목금토일"
POSITION = {DutyRole.CHARGE: "차지", DutyRole.NEW: "신규", DutyRole.NIGHT_ONLY: "나이트전담"}
LEGEND = [(Duty.D, "D 데이"), (Duty.E, "E 이브닝"), (Duty.N, "N 나이트"), (Duty.O, "O 휴무"), (Duty.AL, "AL 연차")]
LEGEND_LABEL = "범례"  # 이 행부터는 근무표가 아니라 설명이라 가져오기에서 읽지 않는다
HEADER_SCAN_ROWS = 10  # 제목 줄 등이 위에 있어도 머리글 행(일자 숫자)을 찾는 범위
TITLE_ROWS = 1
HEADER_ROWS = 2  # 일자 + 요일
NAME_COLS = 2  # 이름 · 직급
TOTALS = [("D", Duty.D), ("E", Duty.E), ("N", Duty.N), ("OFF", Duty.O), ("AL", Duty.AL)]
# 엑셀 코드 → 듀티 제안값. 나머지는 사용자가 매핑
SUMMARY_ROW = re.compile(r"[DEN] 인원 \(필요 \d+\)")  # 내보내기 파일의 인원 합계 행
ALIASES = {"OFF": Duty.O, "/": Duty.O, "휴": Duty.O, "DAY": Duty.D, "EVE": Duty.E, "NIGHT": Duty.N}


def export_schedule(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID) -> tuple[bytes, str]:
    m = members.require_head(db, user_id)
    s = schedules.find(db, m, schedule_id)
    r = schedules.roster(db, s)
    ward = db.get(Ward, m.ward_id)
    wb = Workbook()
    ws = wb.active
    ws.title = s.year_month
    _write_schedule(ws, r, f"{r.month.year}년 {r.month.month}월 근무표 · {ward.ward_name}")
    # 내보내기는 정보 유출 경로이므로 감사 로그 필수
    audit.log(db, m.ward_id, user_id, "SCHEDULE_EXPORTED", "SCHEDULE", s.id)
    return _save(wb), f"schedule-{s.year_month}.xlsx"


def _write_schedule(ws, r, title: str) -> None:
    """제목 · 일자/요일 머리글 · 간호사 행(직급, 합계 열) · 날짜별 인원 행 · 범례. A4 가로 한 장 인쇄"""
    days = r.days()
    first_day = NAME_COLS + 1
    last_day = NAME_COLS + len(days)
    last_col = last_day + len(TOTALS)
    side = Side(style="thin", color=LINE)
    box = Border(left=side, right=side, top=side, bottom=side)
    center = Alignment(horizontal="center", vertical="center")

    def style(c, fill=None, ink="000000", bold=False, size=11, align=center):
        c.border, c.alignment, c.font = box, align, Font(bold=bold, color=ink, size=size)
        if fill:
            c.fill = PatternFill("solid", fgColor=fill)

    def day_tone(d):
        # 공휴일은 요일과 상관없이 일요일과 같은 색
        return SUN if d in r.holidays or d.weekday() == 6 else SAT if d.weekday() == 5 else None

    ws.cell(1, 1, title)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    ws.cell(1, 1).font, ws.cell(1, 1).alignment = Font(bold=True, size=16), Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 30

    head, week = TITLE_ROWS + 1, TITLE_ROWS + 2
    for col, label in enumerate(["이름", "직급"], 1):
        ws.cell(head, col, label)
        ws.merge_cells(start_row=head, start_column=col, end_row=week, end_column=col)
    for i, label in enumerate(t[0] for t in TOTALS):
        col = last_day + 1 + i
        ws.cell(head, col, label)
        ws.merge_cells(start_row=head, start_column=col, end_row=week, end_column=col)
    for i, d in enumerate(days):
        ws.cell(head, first_day + i, d.day)  # 가져오기가 일자 숫자로 열을 찾는다
        ws.cell(week, first_day + i, WEEKDAYS[d.weekday()])
    for row in (head, week):
        for col in range(1, last_col + 1):
            c = ws.cell(row, col)
            tone = day_tone(days[col - first_day]) if first_day <= col <= last_day else None
            style(c, *(tone or (HEAD_FILL, "000000")), bold=True)

    for n in r.nurses:
        row = [r.cell(n.id, d) for d in days]
        position = ("수간호사" if n.role == Role.HEAD_NURSE else "간호조무사"
                    if n.staff_type == StaffType.NURSING_ASSISTANT else POSITION.get(n.duty_role, "간호사"))
        ws.append([n.name, position]
                  + [str(x) if x else None for x in row] + [row.count(duty) for _, duty in TOTALS])
        line = ws.max_row
        # openpyxl은 "="로 시작하는 문자열을 수식으로 저장한다. 이름은 항상 텍스트로 (수식 주입 방지)
        ws.cell(line, 1).data_type = "s"
        for col in range(1, last_col + 1):
            style(ws.cell(line, col), bold=col == 1, align=Alignment(horizontal="left" if col == 1 else "center",
                                                                       vertical="center", indent=1 if col == 1 else 0))
        for i, (x, d) in enumerate(zip(row, days)):
            c = ws.cell(line, first_day + i)
            tone = day_tone(d)
            if x in SOLID:
                style(c, SOLID[x], "FFFFFF", bold=True)
            elif x:  # O·ED
                style(c, OFF_FILL, OFF_INK)
            elif tone:  # 빈 칸도 주말·공휴일 열은 옅게 구분
                style(c, tone[0])
        ws.row_dimensions[line].height = 21

    for duty in (Duty.D, Duty.E, Duty.N):
        need = r.rules.required(duty)
        ws.append([f"{duty} 인원 (필요 {need})", None]
                  + [sum(1 for n in r.nurses if n.staff_type == StaffType.NURSE
                         and r.cell(n.id, d) == duty and n.affiliated(d)) for d in days])
        line = ws.max_row
        ws.merge_cells(start_row=line, start_column=1, end_row=line, end_column=NAME_COLS)
        for col in range(1, last_col + 1):
            c = ws.cell(line, col)
            short = first_day <= col <= last_day and c.value < need
            style(c, "FFEBEE" if short else HEAD_FILL, "F04452" if short else "000000", bold=col == 1 or short)
        ws.row_dimensions[line].height = 20

    legend = ws.max_row + 2
    ws.cell(legend, 1, LEGEND_LABEL).font = Font(bold=True)
    for i, (duty, label) in enumerate(LEGEND):
        c = ws.cell(legend, 2 + 2 * i, label)
        ws.merge_cells(start_row=legend, start_column=2 + 2 * i, end_row=legend, end_column=3 + 2 * i)
        solid = duty in SOLID
        c.fill = PatternFill("solid", fgColor=SOLID.get(duty, OFF_FILL))
        c.font = Font(bold=solid, color="FFFFFF" if solid else OFF_INK)
        c.alignment = center

    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 11
    for col in range(first_day, last_day + 1):
        ws.column_dimensions[get_column_letter(col)].width = 4.6
    for col in range(last_day + 1, last_col + 1):
        ws.column_dimensions[get_column_letter(col)].width = 5.5
    ws.freeze_panes = ws.cell(week + 1, first_day)  # 이름·직급과 일자 머리글은 스크롤해도 보인다
    ws.print_area = f"A1:{get_column_letter(last_col)}{legend}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = ws.page_setup.fitToHeight = 1
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = ws.page_margins.top = ws.page_margins.bottom = 0.4


def create_preview(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID, data: bytes) -> dict:
    """
    행=간호사(첫 열 이름), 열=날짜(첫 행 일자). 근무표에는 아직 반영하지 않는다.
    동명이인·미등록 이름은 자동 매칭하지 않고 nurseMappings로 지정받는다. 보호 셀(AL·소속 기간 밖)은 가져오지 않는다
    """
    m = members.require_head(db, user_id)
    s = schedules.find(db, m, schedule_id)
    r = schedules.roster(db, s)
    by_name: dict[str, list] = {}
    for n in r.nurses:
        by_name.setdefault(n.name.strip(), []).append(n.id)
    ws = _open(data)
    # 제목 줄이 위에 있을 수 있어 일자 숫자가 있는 첫 행을 머리글로 본다
    day_cols, header_row = {}, ws.min_row
    for header in ws.iter_rows(min_row=ws.min_row, max_row=ws.min_row + HEADER_SCAN_ROWS - 1):
        candidate = {}
        for c in header:
            t = _text(c.value).removesuffix("일")
            if t.isdigit() and 1 <= int(t) <= len(r.days()):
                candidate[c.column] = r.month.replace(day=int(t))
        # 제목에 월 숫자가 따로 들어간 경우를 머리글로 오인하지 않도록 이름 열과 여러 날짜를 함께 확인한다.
        if _text(header[0].value) in ("이름", "간호사", "성명") and len(candidate) >= 2:
            day_cols = candidate
            header_row = header[0].row
            break
    if not day_cols:
        raise unprocessable("INVALID_EXCEL_FORMAT", "첫 행에서 날짜 열을 찾지 못했습니다")
    rows, cells, nurse_map, duty_map = [], [], {}, {}
    for row in ws.iter_rows(min_row=header_row + 1):
        name = _text(row[0].value) if row else ""
        if name == LEGEND_LABEL:
            break
        if not name or SUMMARY_ROW.fullmatch(name):  # 요일 줄은 이름 칸이 비어 있어 여기서 건너뛴다
            continue
        row_no = row[0].row
        candidates = by_name.get(name, [])
        rows.append({"rowNumber": row_no, "name": name, "candidates": [str(x) for x in candidates]})
        if len(candidates) == 1:
            nurse_map[str(row_no)] = str(candidates[0])
        for c in row:
            # AL은 연차 승인으로만 생기므로 가져오지 않는다 (기존 AL 셀은 반영 시에도 보호됨)
            if c.column in day_cols and (raw := _text(c.value).upper()) and raw != Duty.AL:
                cells.append({"rowNumber": row_no, "date": day_cols[c.column].isoformat(), "raw": raw})
                if raw not in duty_map and (suggested := _suggest(raw)) is not None:
                    duty_map[raw] = suggested
    p = ImportPreview(ward_id=m.ward_id, schedule_id=s.id, rows=rows, cells=cells, nurse_mappings=nurse_map,
                      duty_mappings=duty_map, created_by=user_id)
    db.add(p)
    db.flush()
    return _view(p)


def update_preview(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID, preview_id: uuid.UUID,
                   nurse_mappings: list[tuple[int, uuid.UUID | None]] | None,
                   duty_mappings: list[tuple[str, Duty | None]] | None) -> dict:
    """nurseId=null은 그 행을 건너뜀, dutyCode=null은 미배정으로 반영"""
    m = members.require_head(db, user_id)
    p = _preview(db, m, schedule_id, preview_id)
    rows = {r["rowNumber"] for r in p.rows}
    nurse_ids = {n.id for n in schedules.roster(db, schedules.find(db, m, schedule_id)).nurses}
    nm, dm = dict(p.nurse_mappings), dict(p.duty_mappings)
    for row_no, nurse_id in nurse_mappings or []:
        if row_no not in rows or (nurse_id is not None and nurse_id not in nurse_ids):
            raise unprocessable("INVALID_MAPPING", f"매핑할 수 없는 행 또는 간호사입니다: {row_no}")
        nm[str(row_no)] = str(nurse_id) if nurse_id else None
    codes = {c["raw"] for c in p.cells}
    for raw, duty in duty_mappings or []:
        if raw not in codes or duty == Duty.AL:
            raise unprocessable("INVALID_MAPPING", f"매핑할 수 없는 코드입니다: {raw} (AL은 연차 승인으로만)")
        dm[raw] = duty
    p.nurse_mappings, p.duty_mappings = nm, dm
    return _view(p)


def apply_preview(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID, preview_id: uuid.UUID, base_version: int,
                  lock_token: str | None) -> dict:
    m = members.require_head(db, user_id)
    p = _preview(db, m, schedule_id, preview_id)
    unresolved = _unresolved(p)
    if unresolved["rows"] or unresolved["codes"]:
        raise unprocessable("UNRESOLVED_MAPPING", "매핑되지 않은 행 또는 코드가 있습니다", unresolved)
    s = schedules.find(db, m, schedule_id)
    schedules.editable(db, s, m, lock_token, base_version)
    r = schedules.roster(db, s)
    nurses = {n.id: n for n in r.nurses}
    changes, seen = [], set()
    for c in p.cells:
        nurse_id = p.nurse_mappings.get(str(c["rowNumber"]))
        if nurse_id is None:
            continue
        nurse_id, d = uuid.UUID(nurse_id), date.fromisoformat(c["date"])
        # 보호 셀(AL·소속 기간 밖)은 그대로 둔다. 같은 간호사가 여러 행에 있으면 첫 행만
        if (nurse_id, d) in seen or r.cell(nurse_id, d) == Duty.AL or not nurses[nurse_id].affiliated(d):
            continue
        seen.add((nurse_id, d))
        duty = p.duty_mappings[c["raw"]]
        changes.append((nurse_id, d, Duty(duty) if duty else None))
    changed = schedules.apply_changes(db, s, changes)
    p.applied_at = now()
    audit.log(db, m.ward_id, user_id, "SCHEDULE_IMPORTED", "SCHEDULE", s.id, None, f"{changed} cells")
    return {"schedule": schedules.view(db, s, m), "violations": schedules.check(db, s)}


def _preview(db: Session, m: members.Member, schedule_id: uuid.UUID, preview_id: uuid.UUID) -> ImportPreview:
    p = db.get(ImportPreview, preview_id)
    if p is None or p.ward_id != m.ward_id or p.schedule_id != schedule_id:
        raise not_found("PREVIEW_NOT_FOUND")
    if p.applied_at is not None:
        raise ApiException(409, "INVALID_RESOURCE_STATE", "이미 반영된 미리보기입니다")
    return p


def _unresolved(p: ImportPreview) -> dict:
    return {"rows": [r["rowNumber"] for r in p.rows if str(r["rowNumber"]) not in p.nurse_mappings],
            "codes": sorted({c["raw"] for c in p.cells} - set(p.duty_mappings))}


def _view(p: ImportPreview) -> dict:
    return {"id": p.id, "scheduleId": p.schedule_id, "status": "APPLIED" if p.applied_at else "READY",
            "rows": p.rows, "cells": p.cells,
            "nurseMappings": [{"rowNumber": int(k), "nurseId": v} for k, v in sorted(p.nurse_mappings.items(),
                                                                                    key=lambda x: int(x[0]))],
            "dutyMappings": [{"raw": k, "dutyCode": v} for k, v in sorted(p.duty_mappings.items())],
            "unresolved": _unresolved(p), "createdAt": p.created_at.isoformat() + "Z"}


def _suggest(raw: str) -> Duty | None:
    if raw in ALIASES:
        return ALIASES[raw]
    try:
        d = Duty(raw)
    except ValueError:
        return None
    return None if d == Duty.AL else d  # AL은 연차 승인으로만 (COM-03)


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _open(data: bytes):
    try:
        return load_workbook(io.BytesIO(data), data_only=True).worksheets[0]
    except Exception:  # 깨진 파일·xlsx가 아닌 파일은 종류가 다양한 예외로 실패한다
        raise unprocessable("INVALID_EXCEL_FORMAT", "엑셀 형식을 읽을 수 없습니다") from None


def _save(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
