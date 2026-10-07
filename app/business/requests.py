"""REQ-01~04"""
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.business import audit, members, notifications, schedules
from app.business.common import (bad_request, conflict, iso, not_found, page, parse_month, require_reason,
                                 today_kst, unprocessable)
from app.business.members import Member
from app.domain.model import Duty, NotificationType, RequestStatus, RequestType
from app.persistence.db import now
from app.persistence.models import Nurse, WorkRequest

# 🔶 사유 코드 운영 목록 미확정 — 명세 예시값 사용
REASON_CODES = ["PERSONAL", "FAMILY", "HEALTH", "STUDY", "ETC"]
# 🔶 간호사 1인·1개월 희망 오프 최대 일수 (권장값)
MAX_PREFERRED_OFF_DAYS = 4
TYPE_LABEL = {RequestType.ANNUAL_LEAVE: "연차", RequestType.PREFERRED_OFF: "희망 오프",
              RequestType.PREFERRED_SHIFT: "희망 근무"}
ACTIVE = (RequestStatus.PENDING, RequestStatus.APPROVED)


def create(db: Session, user_id: uuid.UUID, type_: RequestType, dates: list[date], reason_code: str,
           reason_detail: str | None, preferred_duty: Duty | None) -> dict:
    m = members.require(db, user_id)
    if not dates or len(set(dates)) != len(dates):
        raise bad_request("VALIDATION_ERROR", "대상 날짜는 1개 이상, 중복 없이 입력하세요", "targetDates")
    if len({d.replace(day=1) for d in dates}) != 1:
        raise bad_request("VALIDATION_ERROR", "대상 날짜는 같은 달이어야 합니다", "targetDates")
    if reason_code not in REASON_CODES:
        raise bad_request("VALIDATION_ERROR", f"사유 코드: {REASON_CODES}", "reasonCode")
    if reason_code == "ETC" and not (reason_detail or "").strip():
        raise bad_request("VALIDATION_ERROR", "기타 사유는 상세 내용이 필요합니다", "reasonDetail")
    if type_ == RequestType.PREFERRED_SHIFT and (preferred_duty is None or not preferred_duty.is_work):
        raise bad_request("VALIDATION_ERROR", "희망 근무는 D/E/N 중 하나여야 합니다", "preferredDuty")
    if min(dates) < today_kst():
        raise unprocessable("PAST_DATE", "지난 날짜는 신청할 수 없습니다")
    if schedules.is_confirmed(db, m.ward_id, dates[0]):
        raise conflict("SCHEDULE_CONFIRMED", "이미 확정된 월입니다")
    ym = dates[0].strftime("%Y-%m")
    mine = list(db.scalars(select(WorkRequest).where(WorkRequest.nurse_id == m.nurse_id, WorkRequest.year_month == ym,
                                                     WorkRequest.status.in_(ACTIVE))))
    taken = {d for r in mine for d in r.dates()}
    if taken & set(dates):
        raise conflict("DUPLICATE_REQUEST", "같은 날짜에 이미 신청이 있습니다",
                       sorted(d.isoformat() for d in taken & set(dates)))
    if type_ == RequestType.PREFERRED_OFF:
        used = sum(len(r.target_dates) for r in mine if r.type == RequestType.PREFERRED_OFF)
        if used + len(dates) > MAX_PREFERRED_OFF_DAYS:
            raise unprocessable("REQUEST_LIMIT_EXCEEDED", f"희망 오프는 한 달 {MAX_PREFERRED_OFF_DAYS}일까지입니다",
                                {"limit": MAX_PREFERRED_OFF_DAYS, "used": used})
    r = WorkRequest(ward_id=m.ward_id, nurse_id=m.nurse_id, type=type_, year_month=ym,
                    target_dates=sorted(d.isoformat() for d in dates), reason_code=reason_code,
                    reason_detail=(reason_detail or "").strip() or None,
                    preferred_duty=preferred_duty if type_ == RequestType.PREFERRED_SHIFT else None)
    db.add(r)
    db.flush()
    return view(db, r)


def get(db: Session, user_id: uuid.UUID, request_id: uuid.UUID) -> dict:
    m = members.require(db, user_id)
    return view(db, _get(db, m, request_id, own_only=not m.is_head))


def cancel(db: Session, user_id: uuid.UUID, request_id: uuid.UUID) -> dict:
    """REQ-03: 근무표 확정 전까지 본인만 취소. 승인된 연차는 초안에서 AL을 지운다"""
    m = members.require(db, user_id)
    r = _get(db, m, request_id, own_only=True)
    if r.status not in ACTIVE:
        raise conflict("REQUEST_NOT_CANCELLABLE", "취소할 수 없는 상태입니다")
    if schedules.is_confirmed(db, m.ward_id, r.dates()[0]):
        raise conflict("SCHEDULE_CONFIRMED", "확정된 근무표의 신청은 취소할 수 없습니다")
    if r.status == RequestStatus.APPROVED and r.type == RequestType.ANNUAL_LEAVE:
        schedules.sync_leave(db, m.ward_id, r.nurse_id, r.dates(), False)
    r.status = RequestStatus.CANCELLED
    db.flush()
    return view(db, r)


def approve(db: Session, user_id: uuid.UUID, request_id: uuid.UUID) -> dict:
    m, r = _decidable(db, user_id, request_id)
    # 확정본에는 반영할 수 없으므로 승인을 막는다 (반려는 허용)
    if schedules.is_confirmed(db, m.ward_id, r.dates()[0]):
        raise conflict("SCHEDULE_CONFIRMED", "확정된 근무표의 신청입니다. 확정 취소 후 승인하세요")
    r.status, r.processed_by, r.processed_at = RequestStatus.APPROVED, user_id, now()
    impact = (schedules.sync_leave(db, m.ward_id, r.nurse_id, r.dates(), True)
              if r.type == RequestType.ANNUAL_LEAVE else None)
    db.flush()
    audit.log(db, m.ward_id, user_id, "REQUEST_APPROVED", "WORK_REQUEST", r.id, "PENDING", "APPROVED")
    _notify(db, r, "승인되었습니다")
    return {"request": view(db, r), "scheduleImpact": impact}


def reject(db: Session, user_id: uuid.UUID, request_id: uuid.UUID, reason: str | None) -> dict:
    reason = require_reason(reason)
    m, r = _decidable(db, user_id, request_id)
    r.status, r.processed_by, r.processed_at, r.rejection_reason = RequestStatus.REJECTED, user_id, now(), reason
    db.flush()
    audit.log(db, m.ward_id, user_id, "REQUEST_REJECTED", "WORK_REQUEST", r.id, "PENDING", "REJECTED")
    _notify(db, r, f"반려되었습니다. 사유: {reason}")
    return view(db, r)


def mine(db: Session, user_id: uuid.UUID, ym: str | None, type_: RequestType | None, status: RequestStatus | None,
         page_no: int, size: int) -> dict:
    m = members.require(db, user_id)
    return _search(db, m, m.nurse_id, ym, type_, status, page_no, size)


def ward(db: Session, user_id: uuid.UUID, applicant_id: uuid.UUID | None, ym: str | None, type_: RequestType | None,
         status: RequestStatus | None, page_no: int, size: int) -> dict:
    m = members.require_head(db, user_id)
    return _search(db, m, applicant_id, ym, type_, status, page_no, size)


def view(db: Session, r: WorkRequest) -> dict:
    n = db.get(Nurse, r.nurse_id)
    return {"id": r.id, "applicantId": r.nurse_id, "applicantName": n.name if n else None, "type": r.type,
            "targetDates": r.target_dates, "reasonCode": r.reason_code, "reasonDetail": r.reason_detail,
            "preferredDuty": r.preferred_duty, "status": r.status, "processorId": r.processed_by,
            "processedAt": iso(r.processed_at), "rejectionReason": r.rejection_reason, "createdAt": iso(r.created_at)}


def _search(db: Session, m: Member, nurse_id, ym, type_, status, page_no: int, size: int) -> dict:
    q = select(WorkRequest).where(WorkRequest.ward_id == m.ward_id)
    if nurse_id is not None:
        q = q.where(WorkRequest.nurse_id == nurse_id)
    if type_:
        q = q.where(WorkRequest.type == type_)
    if status:
        q = q.where(WorkRequest.status == status)
    if ym:
        q = q.where(WorkRequest.year_month == parse_month(ym).strftime("%Y-%m"))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(WorkRequest.created_at.desc(), WorkRequest.id).offset(page_no * size).limit(size))
    return page([view(db, r) for r in rows], page_no, size, total)


def _get(db: Session, m: Member, request_id: uuid.UUID, own_only: bool) -> WorkRequest:
    r = db.get(WorkRequest, request_id)
    if r is None or r.ward_id != m.ward_id or (own_only and r.nurse_id != m.nurse_id):
        raise not_found("REQUEST_NOT_FOUND")
    return r


def _decidable(db: Session, user_id: uuid.UUID, request_id: uuid.UUID) -> tuple[Member, WorkRequest]:
    m = members.require_head(db, user_id)
    r = _get(db, m, request_id, own_only=False)
    if r.status != RequestStatus.PENDING:
        raise conflict("REQUEST_ALREADY_PROCESSED", "이미 처리된 신청입니다")
    return m, r


def _notify(db: Session, r: WorkRequest, result: str) -> None:
    label = TYPE_LABEL[r.type]
    notifications.notify_user(db, db.get(Nurse, r.nurse_id).user_id, NotificationType.REQUEST_RESULT,
                              f"{label} 신청 결과", f"{', '.join(r.target_dates)} {label} 신청이 {result}",
                              "WORK_REQUEST", r.id)
