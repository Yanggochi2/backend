"""AUTH-03·04·05·06·07"""
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.business import audit, members, notifications, rules
from app.business.common import (RateLimiter, conflict, iso, not_found, page, require_reason, today_kst,
                                 unprocessable)
from app.domain.model import NotificationType, NurseStatus, Preset, RequestStatus, Role
from app.persistence.db import now
from app.persistence.models import MembershipRequest, Nurse, User, Ward

CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
# AUTH-04 코드 무차별 대입 방지: 계정당 시간당 10회 (🔶 권장값)
_join_attempts = RateLimiter(10, timedelta(hours=1))


def view(db: Session, ward_id: uuid.UUID) -> dict:
    w = db.get(Ward, ward_id)
    return {"id": w.id, "hospitalName": w.hospital_name, "wardName": w.ward_name,
            "requiredStaff": rules.staffing(db, w.id), "createdAt": iso(w.created_at)}


def get(db: Session, user_id: uuid.UUID) -> dict:
    return view(db, members.require(db, user_id).ward_id)


def create(db: Session, user_id: uuid.UUID, hospital_name: str, ward_name: str, staff: dict, preset: Preset) -> dict:
    """AUTH-03. 개설자가 HEAD_NURSE가 된다"""
    if members.membership(db, user_id):
        raise conflict("MEMBERSHIP_ALREADY_EXISTS", "이미 소속된 병동이 있습니다")
    # 대기 중인 신청이 나중에 승인되면 두 병동에 소속되므로 막는다
    if _pending(db, user_id):
        raise conflict("REQUEST_ALREADY_EXISTS", "승인 대기 중인 가입 신청이 있습니다")
    rules.check_staffing(staff)
    w = Ward(hospital_name=hospital_name, ward_name=ward_name, code=_new_code(db))
    db.add(w)
    db.flush()
    rules.seed(db, w.id, preset, staff)
    n = Nurse(ward_id=w.id, user_id=user_id, name=db.get(User, user_id).name, role=Role.HEAD_NURSE,
              joined_at=today_kst(), affiliation_start=today_kst())
    db.add(n)
    db.flush()
    audit.log(db, w.id, user_id, "WARD_CREATED", "WARD", w.id, None, f"{hospital_name} {ward_name}")
    audit.log(db, w.id, user_id, "JOIN_CODE_ISSUED", "WARD", w.id)
    return {"ward": view(db, w.id), "membership": members.view(n), "joinCode": _code_view(w)}


def request_join(db: Session, user_id: uuid.UUID, code: str) -> dict:
    """AUTH-04. 응답에 신청한 병동 이름을 담아 승인 대기 화면에 표시한다"""
    _join_attempts.check(user_id)
    _join_attempts.record(user_id)
    if members.membership(db, user_id):
        raise conflict("MEMBERSHIP_ALREADY_EXISTS", "이미 소속된 병동이 있습니다")
    if _pending(db, user_id):
        raise conflict("REQUEST_ALREADY_EXISTS", "승인 대기 중인 신청이 있습니다")
    w = db.scalar(select(Ward).where(Ward.code == (code or "").strip().upper()))
    if w is None:
        raise not_found("JOIN_CODE_NOT_FOUND")
    j = MembershipRequest(ward_id=w.id, user_id=user_id)
    db.add(j)
    db.flush()
    audit.log(db, w.id, user_id, "MEMBERSHIP_REQUESTED", "MEMBERSHIP_REQUEST", j.id)
    return _request_view(db, j)


def latest_request(db: Session, user_id: uuid.UUID) -> dict | None:
    j = db.scalars(select(MembershipRequest).where(MembershipRequest.user_id == user_id)
                   .order_by(MembershipRequest.created_at.desc())).first()
    return _request_view(db, j) if j else None


def list_requests(db: Session, user_id: uuid.UUID, status: RequestStatus | None, page_no: int, size: int) -> dict:
    """candidates: 이름이 같고 계정이 없는 기존 간호사. 승인 시 nurseId로 지정하면 그 행에 계정을 연결한다"""
    m = members.require_head(db, user_id)
    q = select(MembershipRequest).where(MembershipRequest.ward_id == m.ward_id)
    if status:
        q = q.where(MembershipRequest.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    unlinked = db.scalars(select(Nurse).where(Nurse.ward_id == m.ward_id, Nurse.user_id.is_(None),
                                              Nurse.status != NurseStatus.RETIRED)).all()
    out = []
    for j in db.scalars(q.order_by(MembershipRequest.created_at.desc()).offset(page_no * size).limit(size)):
        v = _request_view(db, j)
        v["candidates"] = [n.id for n in unlinked if n.name == v["name"]] if j.status == RequestStatus.PENDING else []
        out.append(v)
    return page(out, page_no, size, total)


def approve(db: Session, user_id: uuid.UUID, request_id: uuid.UUID, link_nurse_id: uuid.UUID | None) -> dict:
    """
    AUTH-06. 승인으로 부여되는 역할은 NURSE 고정. 간호사 행 생성과 신청 상태 변경은 한 트랜잭션.
    link_nurse_id가 있으면 수간호사가 미리 등록한 (계정 없는) 간호사 행에 계정을 연결한다
    """
    m, j = _decidable(db, user_id, request_id)
    if members.membership(db, j.user_id):
        raise conflict("MEMBERSHIP_ALREADY_EXISTS", "이미 병동에 소속된 사용자입니다")
    if link_nurse_id is not None:
        n = db.get(Nurse, link_nurse_id)
        if n is None or n.ward_id != m.ward_id:
            raise not_found("NURSE_NOT_FOUND")
        if n.user_id is not None or n.status == NurseStatus.RETIRED:
            raise unprocessable("NURSE_NOT_LINKABLE", "계정이 없는 재직 간호사에게만 연결할 수 있습니다")
        n.user_id, n.role = j.user_id, Role.NURSE
    else:
        n = Nurse(ward_id=m.ward_id, user_id=j.user_id, name=db.get(User, j.user_id).name,
                  joined_at=today_kst(), affiliation_start=today_kst())
        db.add(n)
    j.status, j.processed_at, j.processed_by = RequestStatus.APPROVED, now(), user_id
    db.flush()
    audit.log(db, m.ward_id, user_id, "MEMBERSHIP_APPROVED", "MEMBERSHIP_REQUEST", j.id, None, f"nurse:{n.id}")
    w = db.get(Ward, m.ward_id)
    notifications.notify_user(db, j.user_id, NotificationType.MEMBERSHIP_APPROVED, "병동 가입 승인",
                              f"{w.ward_name} 가입이 승인되었습니다", "WARD", w.id)
    return members.view(n)


def reject(db: Session, user_id: uuid.UUID, request_id: uuid.UUID, reason: str | None) -> dict:
    reason = require_reason(reason)
    m, j = _decidable(db, user_id, request_id)
    j.status, j.processed_at, j.processed_by, j.rejection_reason = RequestStatus.REJECTED, now(), user_id, reason
    db.flush()
    audit.log(db, m.ward_id, user_id, "MEMBERSHIP_REJECTED", "MEMBERSHIP_REQUEST", j.id)
    w = db.get(Ward, m.ward_id)
    notifications.notify_user(db, j.user_id, NotificationType.MEMBERSHIP_REJECTED, "병동 가입 반려",
                              f"{w.ward_name} 가입이 반려되었습니다. 사유: {reason}", "MEMBERSHIP_REQUEST", j.id)
    return _request_view(db, j)


def join_code(db: Session, user_id: uuid.UUID) -> dict:
    return _code_view(db.get(Ward, members.require_head(db, user_id).ward_id))


def rotate_code(db: Session, user_id: uuid.UUID) -> dict:
    """AUTH-05. 기존 코드는 즉시 무효. 동시 재발급은 Ward 행 갱신 충돌로 409가 된다"""
    m = members.require_head(db, user_id)
    w = db.get(Ward, m.ward_id)
    w.code, w.code_issued_at = _new_code(db), now()
    audit.log(db, w.id, user_id, "JOIN_CODE_ISSUED", "WARD", w.id)
    return _code_view(w)


def grant_head(db: Session, user_id: uuid.UUID, nurse_id: uuid.UUID) -> dict:
    """AUTH-07 추가 부여"""
    m = members.require_head(db, user_id)
    target = _eligible(db, m, nurse_id)
    if target.role == Role.HEAD_NURSE:
        raise conflict("ROLE_ALREADY_ASSIGNED", "이미 수간호사입니다")
    target.role = Role.HEAD_NURSE
    audit.log(db, m.ward_id, user_id, "HEAD_NURSE_GRANTED", "NURSE", nurse_id, Role.NURSE, Role.HEAD_NURSE)
    return members.view(target)


def transfer_head(db: Session, user_id: uuid.UUID, nurse_id: uuid.UUID) -> dict:
    """AUTH-07 이관: 대상은 HEAD_NURSE, 본인은 NURSE. 두 변경은 한 트랜잭션"""
    m = members.require_head(db, user_id)
    if nurse_id == m.nurse_id:
        raise conflict("LAST_HEAD_NURSE_CONFLICT", "본인에게 이관하면 수간호사가 남지 않습니다")
    target = _eligible(db, m, nurse_id)
    me = db.get(Nurse, m.nurse_id)
    target.role, me.role = Role.HEAD_NURSE, Role.NURSE
    audit.log(db, m.ward_id, user_id, "HEAD_NURSE_TRANSFERRED", "NURSE", nurse_id, f"nurse:{me.id}", f"nurse:{nurse_id}")
    return {"from": members.view(me), "to": members.view(target)}


def count_heads(db: Session, ward_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).where(Nurse.ward_id == ward_id, Nurse.role == Role.HEAD_NURSE,
                                                Nurse.status != NurseStatus.RETIRED))


def _eligible(db: Session, m: members.Member, nurse_id: uuid.UUID) -> Nurse:
    target = db.get(Nurse, nurse_id)
    if target is None or target.ward_id != m.ward_id:
        raise not_found("NURSE_NOT_FOUND")
    if target.user_id is None or target.status == NurseStatus.RETIRED:
        raise unprocessable("NURSE_NOT_ELIGIBLE", "계정이 연결된 재직 간호사에게만 부여할 수 있습니다")
    return target


def _decidable(db: Session, user_id: uuid.UUID, request_id: uuid.UUID) -> tuple[members.Member, MembershipRequest]:
    m = members.require_head(db, user_id)
    j = db.get(MembershipRequest, request_id)
    if j is None or j.ward_id != m.ward_id:
        raise not_found("REQUEST_NOT_FOUND")
    if j.status != RequestStatus.PENDING:
        raise conflict("REQUEST_ALREADY_PROCESSED", "이미 처리된 신청입니다")
    return m, j


def _pending(db: Session, user_id: uuid.UUID) -> MembershipRequest | None:
    return db.scalars(select(MembershipRequest).where(MembershipRequest.user_id == user_id,
                                                      MembershipRequest.status == RequestStatus.PENDING)).first()


def _new_code(db: Session) -> str:
    while True:
        c = "".join(secrets.choice(CODE_CHARS) for _ in range(8))
        if not db.scalar(select(Ward.id).where(Ward.code == c)):
            return c


def _code_view(w: Ward) -> dict:
    return {"code": w.code, "issuedAt": iso(w.code_issued_at)}


def _request_view(db: Session, j: MembershipRequest) -> dict:
    u, w = db.get(User, j.user_id), db.get(Ward, j.ward_id)
    return {"id": j.id, "wardId": w.id, "wardName": w.ward_name, "hospitalName": w.hospital_name, "userId": u.id,
            "name": u.name, "email": u.email, "status": j.status, "createdAt": iso(j.created_at),
            "processedAt": iso(j.processed_at), "rejectionReason": j.rejection_reason}
