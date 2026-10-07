"""
API 라우트 (/api/v1). 병동 리소스는 /wards/me 아래에 두고 항상 세션 사용자의 소속 병동이 대상이다 (1.2).
컨트롤러는 요청·응답 변환만 하고 로직은 business 계층에 둔다. 단일 리소스는 {data}, 목록은 {data, meta}
"""
import uuid

from fastapi import APIRouter, Request, Response

from app.business import (auth, nurses, schedules, wards)
from app.domain.model import DutyRole, NurseStatus, RequestStatus, Role, Severity
from app.presentation import schemas as s
from app.presentation.deps import (ACCESS, DB, PREFIX, REFRESH, Page, TxRoute, UserId, clear_auth_cookies,
                                   set_auth_cookies)

api = APIRouter(prefix=PREFIX, route_class=TxRoute)


def _d(x) -> dict:
    return {"data": x}


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
def edit_cells(schedule_id: uuid.UUID, body: s.CellBulkPatch, user_id: UserId, db: DB):
    changes = [(c.nurse_id, c.date, c.duty_code) for c in body.changes]
    return _d(schedules.edit_cells(db, user_id, schedule_id, body.base_version, changes))


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
