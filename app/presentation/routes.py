"""
API 라우트 (/api/v1). 병동 리소스는 /wards/me 아래에 두고 항상 세션 사용자의 소속 병동이 대상이다 (1.2).
컨트롤러는 요청·응답 변환만 하고 로직은 business 계층에 둔다. 단일 리소스는 {data}, 목록은 {data, meta}
"""
import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response, UploadFile

from app.business import (audit, auth, excel, generations, notifications, nurses, requests, rules, schedules, wards)
from app.business.common import ApiException, parse_month
from app.domain.model import DutyRole, NotificationType, NurseStatus, RequestStatus, RequestType, Role, Severity
from app.presentation import schemas as s
from app.presentation.deps import (ACCESS, DB, PREFIX, REFRESH, Page, TxRoute, UserId, clear_auth_cookies,
                                   set_auth_cookies)

api = APIRouter(prefix=PREFIX, route_class=TxRoute)
MAX_UPLOAD = 10 * 1024 * 1024  # 🔶 권장 10MB
LockToken = Annotated[str | None, Header(alias="X-Schedule-Lock-Token")]


def _d(x) -> dict:
    return {"data": x}


def _upload(file: UploadFile) -> bytes:
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise ApiException(413, "FILE_TOO_LARGE", "파일이 너무 큽니다 (최대 10MB)")
    return data


def _utc(dt: datetime | None) -> datetime | None:
    return dt.astimezone(UTC).replace(tzinfo=None) if dt and dt.tzinfo else dt


# --- 인증 (AUTH-01·02)
@api.post("/auth/signup", status_code=201)
def signup(body: s.Signup, db: DB):
    return _d(auth.signup(db, body.name, body.email, body.password))


@api.post("/auth/login")
def login(body: s.Login, db: DB, response: Response):
    user, tokens = auth.login(db, body.email, body.password)
    set_auth_cookies(response, tokens)
    return _d(user)


@api.post("/auth/refresh")
def refresh(request: Request, db: DB, response: Response):
    user, tokens = auth.refresh(db, request.cookies.get(REFRESH))
    set_auth_cookies(response, tokens)
    return _d(user)


@api.post("/auth/logout", status_code=204)
def logout(user_id: UserId, db: DB, request: Request, response: Response):
    auth.logout(db, user_id, request.cookies.get(ACCESS))
    clear_auth_cookies(response)


@api.get("/me")
def me(user_id: UserId, db: DB):
    return _d(auth.me(db, user_id))


# --- 병동·가입 (AUTH-03~07)
@api.post("/wards", status_code=201)
def create_ward(body: s.CreateWard, user_id: UserId, db: DB):
    return _d(wards.create(db, user_id, body.hospital_name, body.ward_name, body.required_staff, body.rule_preset))


@api.get("/wards/me")
def my_ward(user_id: UserId, db: DB):
    return _d(wards.get(db, user_id))


@api.post("/ward-membership-requests", status_code=201)
def request_membership(body: s.JoinRequest, user_id: UserId, db: DB):
    return _d(wards.request_join(db, user_id, body.join_code))


@api.get("/wards/me/membership-requests")
def membership_requests(user_id: UserId, db: DB, p: Page, status: RequestStatus | None = None):
    return wards.list_requests(db, user_id, status, p.page, p.size)


@api.post("/wards/me/membership-requests/{request_id}/approve")
def approve_membership(request_id: uuid.UUID, user_id: UserId, db: DB, body: s.ApproveMembership | None = None):
    return _d(wards.approve(db, user_id, request_id, body.nurse_id if body else None))


@api.post("/wards/me/membership-requests/{request_id}/reject")
def reject_membership(request_id: uuid.UUID, user_id: UserId, db: DB, body: s.Reason | None = None):
    return _d(wards.reject(db, user_id, request_id, body.reason if body else None))


@api.get("/wards/me/join-code")
def join_code(user_id: UserId, db: DB):
    return _d(wards.join_code(db, user_id))


@api.post("/wards/me/join-code/rotate")
def rotate_join_code(user_id: UserId, db: DB):
    return _d(wards.rotate_code(db, user_id))


@api.post("/wards/me/head-nurses/{nurse_id}/grant")
def grant_head(nurse_id: uuid.UUID, user_id: UserId, db: DB):
    return _d(wards.grant_head(db, user_id, nurse_id))


@api.post("/wards/me/head-nurse-transfer")
def transfer_head(body: s.Transfer, user_id: UserId, db: DB):
    return _d(wards.transfer_head(db, user_id, body.target_nurse_id))


# --- 간호사 (NUR)
@api.post("/wards/me/nurses", status_code=201)
def create_nurse(body: s.NurseCreate, user_id: UserId, db: DB):
    return _d(nurses.create(db, user_id, body.model_dump()))


@api.get("/wards/me/nurses")
def list_nurses(user_id: UserId, db: DB, p: Page, q: str | None = None, role: Role | None = None,
                dutyRole: DutyRole | None = None, status: NurseStatus | None = None,  # noqa: N803
                includeRetired: bool = False, sort: str | None = None):  # noqa: N803
    return nurses.list_(db, user_id, q, role, dutyRole, status, includeRetired, sort, p.page, p.size)


@api.get("/wards/me/nurses/{nurse_id}")
def get_nurse(nurse_id: uuid.UUID, user_id: UserId, db: DB):
    return _d(nurses.get(db, user_id, nurse_id))


@api.patch("/wards/me/nurses/{nurse_id}")
def patch_nurse(nurse_id: uuid.UUID, body: s.NursePatch, user_id: UserId, db: DB):
    return _d(nurses.patch(db, user_id, nurse_id, body.model_dump(exclude_unset=True, exclude={"version"}),
                           body.version))


@api.post("/wards/me/nurses/{nurse_id}/retire")
def retire_nurse(nurse_id: uuid.UUID, body: s.Retire, user_id: UserId, db: DB):
    return _d(nurses.retire(db, user_id, nurse_id, body.affiliation_end))


# --- 근무 규칙 (RULE)
@api.get("/wards/me/rules")
def list_rules(user_id: UserId, db: DB):
    return _d(rules.list_(db, user_id))


@api.patch("/wards/me/rules/{rule_id}")
def patch_rule(rule_id: uuid.UUID, body: s.RulePatch, user_id: UserId, db: DB):
    changes = body.model_dump(exclude_unset=True, include={"enabled", "severity", "parameters"})
    return _d(rules.patch(db, user_id, rule_id, changes, body.reason, body.version))


@api.post("/wards/me/rule-presets/{preset_id}/apply")
def apply_preset(preset_id: str, user_id: UserId, db: DB):
    return _d(rules.apply_preset(db, user_id, preset_id))


@api.get("/wards/me/holidays")
def holidays(yearMonth: str, user_id: UserId, db: DB):  # noqa: N803
    return _d(rules.holidays(db, user_id, parse_month(yearMonth)))


@api.put("/wards/me/holidays/{day}")
def put_holiday(day: date, body: s.HolidayPut, user_id: UserId, db: DB):
    return _d(rules.put_holiday(db, user_id, day, body.is_holiday, body.name, body.reason))


@api.get("/wards/me/off-targets/{year_month}")
def get_off_target(year_month: str, user_id: UserId, db: DB):
    return _d(schedules.get_off_target(db, user_id, year_month))


@api.patch("/wards/me/off-targets/{year_month}")
def patch_off_target(year_month: str, body: s.OffTargetPatch, user_id: UserId, db: DB):
    return _d(schedules.patch_off_target(db, user_id, year_month, body.target_count, body.reason))


# --- 근무표 (SCH)
@api.post("/wards/me/schedules", status_code=201)
def create_schedule(body: s.ScheduleCreate, user_id: UserId, db: DB):
    return _d(schedules.create(db, user_id, body.year_month))


@api.get("/wards/me/schedules")
def schedule_by_month(yearMonth: str, user_id: UserId, db: DB):  # noqa: N803
    return _d(schedules.get_by_month(db, user_id, yearMonth))


@api.get("/wards/me/schedules/{schedule_id}")
def get_schedule(schedule_id: uuid.UUID, user_id: UserId, db: DB):
    return _d(schedules.get(db, user_id, schedule_id))


@api.patch("/wards/me/schedules/{schedule_id}/cells")
def edit_cells(schedule_id: uuid.UUID, body: s.CellBulkPatch, user_id: UserId, db: DB, lock: LockToken = None):
    changes = [(c.nurse_id, c.date, c.duty_code) for c in body.changes]
    return _d(schedules.edit_cells(db, user_id, schedule_id, body.base_version, changes, lock))


@api.get("/wards/me/schedules/{schedule_id}/coverage")
def coverage(schedule_id: uuid.UUID, user_id: UserId, db: DB):
    return _d(schedules.coverage(db, user_id, schedule_id))


@api.get("/wards/me/schedules/{schedule_id}/violations")
def violations(schedule_id: uuid.UUID, user_id: UserId, db: DB, p: Page, severity: Severity | None = None,
               nurseId: uuid.UUID | None = None, ruleId: uuid.UUID | None = None):  # noqa: N803
    return schedules.violations(db, user_id, schedule_id, severity, nurseId, ruleId, p.page, p.size)


@api.post("/wards/me/schedules/{schedule_id}/confirm")
def confirm(schedule_id: uuid.UUID, user_id: UserId, db: DB, body: s.Confirm | None = None):
    return _d(schedules.confirm(db, user_id, schedule_id, set(body.acknowledged_soft_violation_ids if body else [])))


@api.post("/wards/me/schedules/{schedule_id}/confirmation-cancellations")
def cancel_confirmation(schedule_id: uuid.UUID, user_id: UserId, db: DB, body: s.Reason | None = None):
    return _d(schedules.cancel_confirmation(db, user_id, schedule_id, body.reason if body else None))


@api.get("/wards/me/schedules/{schedule_id}/export.xlsx")
def export_schedule(schedule_id: uuid.UUID, user_id: UserId, db: DB):
    data, filename = excel.export_schedule(db, user_id, schedule_id)
    return Response(data, media_type=excel.XLSX, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@api.post("/wards/me/schedules/{schedule_id}/import-previews", status_code=202)
def import_preview(schedule_id: uuid.UUID, file: UploadFile, user_id: UserId, db: DB):
    return _d(excel.create_preview(db, user_id, schedule_id, _upload(file)))


@api.patch("/wards/me/schedules/{schedule_id}/import-previews/{preview_id}")
def patch_import_preview(schedule_id: uuid.UUID, preview_id: uuid.UUID, body: s.ImportMappingPatch, user_id: UserId,
                         db: DB):
    nm = [(x.row_number, x.nurse_id) for x in body.nurse_mappings or []]
    dm = [(x.raw, x.duty_code) for x in body.duty_mappings or []]
    return _d(excel.update_preview(db, user_id, schedule_id, preview_id, nm, dm))


@api.post("/wards/me/schedules/{schedule_id}/import-previews/{preview_id}/apply")
def apply_import(schedule_id: uuid.UUID, preview_id: uuid.UUID, body: s.BaseVersion, user_id: UserId, db: DB,
                 lock: LockToken = None):
    return _d(excel.apply_preview(db, user_id, schedule_id, preview_id, body.base_version, lock))


@api.post("/wards/me/schedules/{schedule_id}/lock", status_code=201)
def acquire_lock(schedule_id: uuid.UUID, user_id: UserId, db: DB):
    return _d(schedules.acquire_lock(db, user_id, schedule_id))


@api.delete("/wards/me/schedules/{schedule_id}/lock", status_code=204)
def release_lock(schedule_id: uuid.UUID, user_id: UserId, db: DB, lock: LockToken = None):
    schedules.release_lock(db, user_id, schedule_id, lock)


@api.post("/wards/me/schedules/{schedule_id}/lock/takeover")
def take_over_lock(schedule_id: uuid.UUID, user_id: UserId, db: DB, body: s.Reason | None = None):
    return _d(schedules.take_over_lock(db, user_id, schedule_id, body.reason if body else None))


# --- 자동 생성 (GEN)
@api.post("/wards/me/schedules/{schedule_id}/generations", status_code=202)
def start_generation(schedule_id: uuid.UUID, user_id: UserId, db: DB, body: s.GenerationStart | None = None,
                     lock: LockToken = None):
    body = body or s.GenerationStart()
    fixed = [(c.nurse_id, c.date) for c in body.fixed_cells]
    return _d(generations.start(db, user_id, schedule_id, fixed, body.max_seconds, lock))


@api.get("/wards/me/generations/{job_id}")
def get_generation(job_id: uuid.UUID, user_id: UserId, db: DB):
    return _d(generations.get(db, user_id, job_id))


@api.post("/wards/me/generations/{job_id}/stop")
def stop_generation(job_id: uuid.UUID, user_id: UserId, db: DB, body: s.Stop | None = None):  # noqa: ARG001
    return _d(generations.stop(db, user_id, job_id))


@api.post("/wards/me/generations/{job_id}/relaxations", status_code=202)
def relax_generation(job_id: uuid.UUID, body: s.Relaxations, user_id: UserId, db: DB, lock: LockToken = None):
    return _d(generations.relax(db, user_id, job_id, body.relaxation_ids, lock))


@api.post("/wards/me/generations/{job_id}/partial-result/apply")
def apply_partial(job_id: uuid.UUID, body: s.BaseVersion, user_id: UserId, db: DB, lock: LockToken = None):
    return _d(generations.apply_partial(db, user_id, job_id, body.base_version, lock))


# --- 신청 (REQ). /requests/me는 /requests/{id}보다 먼저 선언
@api.post("/wards/me/requests", status_code=201)
def create_request(body: s.WorkRequestCreate, user_id: UserId, db: DB):
    return _d(requests.create(db, user_id, body.type, body.target_dates, body.reason_code, body.reason_detail,
                              body.preferred_duty))


@api.get("/wards/me/requests/me")
def my_requests(user_id: UserId, db: DB, p: Page, yearMonth: str | None = None,  # noqa: N803
                type: RequestType | None = None, status: RequestStatus | None = None):  # noqa: A002
    return requests.mine(db, user_id, yearMonth, type, status, p.page, p.size)


@api.get("/wards/me/requests")
def ward_requests(user_id: UserId, db: DB, p: Page, applicantId: uuid.UUID | None = None,  # noqa: N803
                  yearMonth: str | None = None, type: RequestType | None = None,  # noqa: N803,A002
                  status: RequestStatus | None = None):
    return requests.ward(db, user_id, applicantId, yearMonth, type, status, p.page, p.size)


@api.get("/wards/me/requests/{request_id}")
def get_request(request_id: uuid.UUID, user_id: UserId, db: DB):
    return _d(requests.get(db, user_id, request_id))


@api.post("/wards/me/requests/{request_id}/approve")
def approve_request(request_id: uuid.UUID, user_id: UserId, db: DB):
    return _d(requests.approve(db, user_id, request_id))


@api.post("/wards/me/requests/{request_id}/reject")
def reject_request(request_id: uuid.UUID, user_id: UserId, db: DB, body: s.Reason | None = None):
    return _d(requests.reject(db, user_id, request_id, body.reason if body else None))


@api.post("/wards/me/requests/{request_id}/cancel")
def cancel_request(request_id: uuid.UUID, user_id: UserId, db: DB):
    return _d(requests.cancel(db, user_id, request_id))


# --- 알림 (NOTI). 본인 것만
@api.get("/me/notifications")
def my_notifications(user_id: UserId, db: DB, p: Page, unreadOnly: bool = False,  # noqa: N803
                     type: NotificationType | None = None):  # noqa: A002
    return notifications.list_(db, user_id, unreadOnly, type, p.page, p.size)


@api.patch("/me/notifications/{notification_id}")
def patch_notification(notification_id: uuid.UUID, body: s.NotificationPatch, user_id: UserId, db: DB):
    return _d(notifications.mark(db, user_id, notification_id, body.read))


@api.get("/me/notification-settings")
def notification_settings(user_id: UserId, db: DB):
    return _d(notifications.settings(db, user_id))


@api.patch("/me/notification-settings")
def patch_notification_settings(body: s.NotificationSettingsPatch, user_id: UserId, db: DB):
    changes = {k: v for k, v in body.model_dump(by_alias=True, exclude_unset=True).items() if v is not None}
    return _d(notifications.update_settings(db, user_id, changes))


# --- 감사 로그 (SEC-03)
@api.get("/wards/me/audit-logs")
def audit_logs(user_id: UserId, db: DB, p: Page, frm: datetime | None = Query(None, alias="from"),
               to: datetime | None = None, actorId: uuid.UUID | None = None,  # noqa: N803
               actionType: str | None = None):  # noqa: N803
    return audit.search(db, user_id, _utc(frm), _utc(to), actorId, actionType, p.page, p.size)
