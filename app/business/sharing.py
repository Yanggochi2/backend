"""공유 토큰은 확정된 한 달의 근무표에 대한 조회 권한만 제공한다."""
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business import audit, members, nurses, schedules
from datetime import timedelta

from app.business.common import ApiException, RateLimiter, bad_request, conflict, not_found, sha256
from app.domain.model import Duty, DutyRole
from app.domain.nurse_sort import sorted_nurses
from app.persistence.models import Schedule, ScheduleShare, Ward

SHARE_CODE_MIN, SHARE_CODE_MAX = 4, 30
ACCESS_COOKIE_PREFIX = "SHARE_"
ACCESS_TTL = timedelta(days=60)  # 🔶 한 번 입력하면 이 기간 동안 다시 묻지 않는다
# 공유 코드 무차별 대입 방지: 링크당 15분에 10회 (🔶 권장값)
_code_failures = RateLimiter(10, timedelta(minutes=15))


def issue(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID) -> dict:
    m = members.require_head(db, user_id)
    schedule = schedules.find(db, m, schedule_id)
    db.refresh(schedule, with_for_update=True)
    if schedule.status not in schedules.VISIBLE_TO_NURSES:
        raise conflict("SCHEDULE_NOT_CONFIRMED", "확정된 근무표만 공유할 수 있습니다")
    token = secrets.token_urlsafe(32)
    share = db.scalar(select(ScheduleShare).where(ScheduleShare.schedule_id == schedule_id))
    if share is None:
        share = ScheduleShare(schedule_id=schedule_id, token_hash=sha256(token))
        db.add(share)
    else:
        share.token_hash = sha256(token)
    db.flush()
    audit.log(db, m.ward_id, user_id, "SCHEDULE_SHARE_ISSUED", "SCHEDULE", schedule_id)
    return {"shareToken": token, "sharePath": f"/shared/{token}", "scheduleId": schedule_id}


def revoke(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID) -> None:
    m = members.require_head(db, user_id)
    schedule = schedules.find(db, m, schedule_id)
    db.refresh(schedule, with_for_update=True)
    share = db.scalar(select(ScheduleShare).where(ScheduleShare.schedule_id == schedule_id))
    if share is not None:
        db.delete(share)
        audit.log(db, m.ward_id, user_id, "SCHEDULE_SHARE_REVOKED", "SCHEDULE", schedule_id)


# --- 공유 링크 1차 보안 코드 (9차 미팅): 수간호사가 병동 단위로 정한 간단한 코드를 알아야 공유 근무표를 본다


def set_code(db: Session, user_id: uuid.UUID, code: str) -> dict:
    m = members.require_head(db, user_id)
    code = code.strip()
    if not SHARE_CODE_MIN <= len(code) <= SHARE_CODE_MAX:
        raise bad_request("VALIDATION_ERROR", f"공유 코드는 {SHARE_CODE_MIN}~{SHARE_CODE_MAX}자입니다", "code")
    w = db.get(Ward, m.ward_id)
    w.share_code_salt = secrets.token_hex(32)
    w.share_code_hash = _code_hash(w.share_code_salt, code)
    audit.log(db, m.ward_id, user_id, "SHARE_CODE_SET", "WARD", w.id)
    return {"shareCodeSet": True}


def clear_code(db: Session, user_id: uuid.UUID) -> None:
    m = members.require_head(db, user_id)
    w = db.get(Ward, m.ward_id)
    if w.share_code_hash is not None:
        w.share_code_hash = w.share_code_salt = None
        audit.log(db, m.ward_id, user_id, "SHARE_CODE_CLEARED", "WARD", w.id)


def access_cookie_name(token: str) -> str:
    """링크마다 다른 쿠키 이름 (여러 병동·여러 달 링크를 함께 써도 서로 덮어쓰지 않게)"""
    return ACCESS_COOKIE_PREFIX + sha256(token)[:16]


def unlock(db: Session, token: str, code: str) -> str | None:
    """코드가 맞으면 공유 접근 쿠키 값을 돌려준다. 병동에 코드가 없으면 None (쿠키 필요 없음)"""
    _, ward = _shared(db, token)
    if ward.share_code_hash is None:
        return None
    key = sha256(token)
    _code_failures.check(key)
    if _code_hash(ward.share_code_salt, code.strip()) != ward.share_code_hash:
        _code_failures.record(key)
        raise ApiException(403, "SHARE_CODE_INVALID", "공유 코드가 맞지 않습니다")
    _code_failures.reset(key)
    return _access_value(ward, token)


def _code_hash(salt: str, code: str) -> str:
    return sha256(f"{salt}:{code}")


def _access_value(ward: Ward, token: str) -> str:
    # salt는 서버에만 있으므로 코드와 토큰을 알아도 밖에서 만들 수 없다. 코드를 바꾸면 salt가 바뀌어 무효가 된다
    return sha256(f"{ward.share_code_salt}:access:{sha256(token)}")


def _shared(db: Session, token: str) -> tuple[Schedule, Ward]:
    share = db.scalar(select(ScheduleShare).where(ScheduleShare.token_hash == sha256(token)))
    schedule = db.get(Schedule, share.schedule_id) if share else None
    if schedule is None or schedule.status not in schedules.VISIBLE_TO_NURSES:
        raise not_found("SCHEDULE_NOT_FOUND")
    return schedule, db.get(Ward, schedule.ward_id)


def get(db: Session, token: str, sort: str | None, q: str | None = None, access: str | None = None) -> dict:
    order = nurses.normalize_sort(sort)
    schedule, ward = _shared(db, token)
    if ward.share_code_hash is not None and access != _access_value(ward, token):
        raise ApiException(401, "SHARE_CODE_REQUIRED", "공유 코드를 입력해 주세요")
    roster = schedules.roster(db, schedule)
    rows = sorted_nurses(roster.nurses, order)
    query = (q or "").strip()
    if query:
        folded = query.casefold()
        rows = [n for n in rows if folded in n.name.casefold()]
    # 화이트리스트 응답. 상태/경력/입사연도/연차 사유/계정/잠금/규칙은 노출하지 않는다.
    # AL/ED도 공개 화면에서는 부재(O)로 통일한다.
    return {"yearMonth": schedule.year_month, "status": schedule.status, "version": schedule.version,
            "defaultView": "month", "defaultSort": "proficiency", "sort": order, "query": query,
            "nurses": [{"id": n.id, "name": n.name, "role": n.role, "staffType": n.staff_type,
                        "dutyRole": DutyRole.GENERAL if n.duty_role == DutyRole.PRECEPTOR else n.duty_role} for n in rows],
            "cells": [{"nurseId": n.id, "date": d.isoformat(),
                       "dutyCode": Duty.O if roster.cell(n.id, d) in (Duty.AL, Duty.ED) else roster.cell(n.id, d),
                       "editable": False} for n in rows for d in roster.days()]}
