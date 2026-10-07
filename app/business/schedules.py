"""SCH-01~11, RULE-03"""
import secrets
import uuid
from datetime import date, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.business import audit, members, notifications, rules
from app.business.common import (ApiException, bad_request, conflict, iso, month_end, not_found, page, parse_month,
                                 require_reason, sha256, unprocessable)
from app.business.members import Member
from app.domain import validator
from app.domain.model import (Cells, Duty, NotificationType, NurseStatus, Roster,
                              ScheduleStatus, Severity, Violation)
from app.persistence.db import now
from app.persistence.models import Assignment, Nurse, Schedule, User

# SCH-11 무활동 자동 해제 (🔶 권장값)
LOCK_TTL = timedelta(minutes=30)
VISIBLE_TO_NURSES = (ScheduleStatus.CONFIRMED, ScheduleStatus.ARCHIVED)


def create(db: Session, user_id: uuid.UUID, ym: str) -> dict:
    """SCH-01"""
    m = members.require_head(db, user_id)
    first = parse_month(ym)
    if by_month(db, m.ward_id, first):
        raise conflict("SCHEDULE_ALREADY_EXISTS", "이미 해당 월 근무표가 있습니다")
    s = Schedule(ward_id=m.ward_id, year_month=first.strftime("%Y-%m"))
    db.add(s)
    db.flush()
    r = roster(db, s)
    if not any(n.status != NurseStatus.RETIRED for n in r.nurses):
        raise unprocessable("NO_ACTIVE_NURSES", "해당 월에 소속된 간호사가 없습니다")
    audit.log(db, m.ward_id, user_id, "SCHEDULE_CREATED", "SCHEDULE", s.id, None, s.year_month)
    return view(db, s, m)


def get_by_month(db: Session, user_id: uuid.UUID, ym: str) -> dict:
    m = members.require(db, user_id)
    s = by_month(db, m.ward_id, parse_month(ym))
    return view(db, _visible(s, m), m)


def get(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID) -> dict:
    m = members.require(db, user_id)
    return view(db, find(db, m, schedule_id), m)


def coverage(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID) -> list[dict]:
    m = members.require(db, user_id)
    return _coverage(roster(db, find(db, m, schedule_id)))


def edit_cells(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID, base_version: int,
               changes: list[tuple[uuid.UUID, date, Duty | None]], lock_token: str | None) -> dict:
    """SCH-02·03. 전체 성공 또는 전체 실패 (요청 트랜잭션)"""
    m = members.require_head(db, user_id)
    s = find(db, m, schedule_id)
    editable(db, s, m, lock_token, base_version)
    changed = apply_changes(db, s, changes)
    audit.log(db, m.ward_id, user_id, "SCHEDULE_CELLS_CHANGED", "SCHEDULE", s.id, None, f"{changed} cells")
    r = roster(db, s)
    return {"version": s.version, "changedCells": changed, "coverage": _coverage(r), "violations": check(db, s, r)}


def apply_changes(db: Session, s: Schedule, changes: list[tuple[uuid.UUID, date, Duty | None]]) -> int:
    """셀 변경 공통. AL 셀·소속 기간 밖 셀은 보호된다 (422 PROTECTED_CELL)"""
    r = roster(db, s)
    by_id = {n.id: n for n in r.nurses}
    existing = {(a.nurse_id, a.date): a for a in _assignments(db, s.id)}
    if len({(nurse_id, d) for nurse_id, d, _ in changes}) != len(changes):
        raise bad_request("VALIDATION_ERROR", "같은 셀이 여러 번 포함되어 있습니다", "changes")
    changed = 0
    for nurse_id, d, duty in changes:
        n = by_id.get(nurse_id)
        if n is None:
            raise not_found("NURSE_NOT_FOUND")
        if d.replace(day=1) != r.month:
            raise bad_request("VALIDATION_ERROR", f"근무표 대상 월의 날짜가 아닙니다: {d}", "changes.date")
        a = existing.get((nurse_id, d))
        if duty == Duty.AL or (a and a.duty == Duty.AL) or not n.affiliated(d):
            raise unprocessable("PROTECTED_CELL", f"편집할 수 없는 셀입니다: {n.name} {d}",
                                {"nurseId": str(nurse_id), "date": d.isoformat()})
        if (a.duty if a else None) == duty:
            continue
        changed += 1
        if duty is None:
            db.delete(a)
            existing.pop((nurse_id, d))
        elif a:
            a.duty = duty
        else:
            existing[(nurse_id, d)] = a = Assignment(schedule_id=s.id, nurse_id=nurse_id, date=d, duty=duty)
            db.add(a)
    touch(db, s)
    return changed


def violations(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID, severity: Severity | None,
               nurse_id: uuid.UUID | None, rule_id: uuid.UUID | None, page_no: int, size: int) -> dict:
    m = members.require_head(db, user_id)
    vs = [v for v in check(db, find(db, m, schedule_id))
          if (severity is None or v["severity"] == severity) and (nurse_id is None or v["nurseId"] == nurse_id)
          and (rule_id is None or v["ruleId"] == rule_id)]
    return page(vs[page_no * size:(page_no + 1) * size], page_no, size, len(vs))


def confirm(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID, acknowledged: set[uuid.UUID]) -> dict:
    """SCH-07. 하드 위반 0 · 모든 소프트 위반 확인(acknowledged)이어야 확정"""
    m = members.require_head(db, user_id)
    s = find(db, m, schedule_id)
    if s.status != ScheduleStatus.DRAFT:
        raise conflict("INVALID_SCHEDULE_STATE", f"초안 상태에서만 확정할 수 있습니다. 현재: {s.status}")
    if _lock_active(s) and s.locked_by != user_id:
        raise ApiException(423, "SCHEDULE_LOCKED", "다른 수간호사가 편집 중입니다", lock_view(db, s))
    vs = check(db, s)
    hard = [v for v in vs if v["severity"] == Severity.HARD]
    if hard:
        raise conflict("HARD_VIOLATIONS_EXIST", f"하드 위반 {len(hard)}건이 있어 확정할 수 없습니다", hard)
    missing = [v for v in vs if v["id"] not in acknowledged]
    if missing:
        raise conflict("SOFT_VIOLATIONS_UNACKNOWLEDGED", f"확인하지 않은 소프트 위반 {len(missing)}건이 있습니다", missing)
    s.status, s.confirmed_at, s.confirmed_by = ScheduleStatus.CONFIRMED, now(), user_id
    s.confirm_count += 1
    _unlock(s)
    touch(db, s)
    for o in db.scalars(select(Schedule).where(Schedule.ward_id == m.ward_id, Schedule.status == ScheduleStatus.CONFIRMED,
                                               Schedule.year_month < s.year_month)):
        o.status = ScheduleStatus.ARCHIVED
    db.flush()
    audit.log(db, m.ward_id, user_id, "SCHEDULE_CONFIRMED", "SCHEDULE", s.id, "DRAFT", f"CONFIRMED soft={len(vs)}")
    again = s.confirm_count > 1
    notifications.notify_ward(db, m.ward_id, NotificationType.SCHEDULE_CONFIRMED, f"{s.year_month} 근무표 확정",
                              f"{s.year_month} 근무표가 다시 확정되었습니다. 변경된 근무를 확인하세요" if again
                              else f"{s.year_month} 근무표가 확정되었습니다", "SCHEDULE", s.id)
    return view(db, s, m)


def cancel_confirmation(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID, reason: str | None) -> dict:
    reason = require_reason(reason)
    m = members.require_head(db, user_id)
    s = find(db, m, schedule_id)
    if s.status == ScheduleStatus.ARCHIVED:
        raise conflict("ARCHIVED_SCHEDULE", "다음 달이 확정되어 보관된 근무표는 취소할 수 없습니다")
    if s.status != ScheduleStatus.CONFIRMED:
        raise conflict("INVALID_SCHEDULE_STATE", "확정 상태가 아닙니다")
    s.status = ScheduleStatus.DRAFT
    s.cancelled_at, s.cancelled_by, s.cancel_reason = now(), user_id, reason
    touch(db, s)
    audit.log(db, m.ward_id, user_id, "SCHEDULE_CONFIRMATION_CANCELLED", "SCHEDULE", s.id, "CONFIRMED",
              f"DRAFT reason={reason}")
    notifications.notify_ward(db, m.ward_id, NotificationType.SCHEDULE_CANCELLED, f"{s.year_month} 근무표 확정 취소",
                              f"{s.year_month} 근무표 확정이 취소되었습니다. 재확정 시 다시 알려드립니다. 사유: {reason}",
                              "SCHEDULE", s.id)
    return view(db, s, m)


# --- 월 OFF 목표 (RULE-03)
def get_off_target(db: Session, user_id: uuid.UUID, ym: str) -> dict:
    m = members.require_head(db, user_id)
    s = by_month(db, m.ward_id, parse_month(ym))
    if s is None:
        raise not_found("TARGET_NOT_FOUND")
    return _off_target_view(db, s)


def patch_off_target(db: Session, user_id: uuid.UUID, ym: str, target: int | None, reason: str | None) -> dict:
    """targetCount=null이면 자동 계산(공휴일 수 + 1)으로 돌아간다"""
    m = members.require_head(db, user_id)
    s = by_month(db, m.ward_id, parse_month(ym))
    if s is None:
        raise not_found("TARGET_NOT_FOUND")
    if s.status in VISIBLE_TO_NURSES:
        raise conflict("SCHEDULE_CONFIRMED", "확정된 근무표의 OFF 목표는 바꿀 수 없습니다")
    if target is not None and not 0 <= target <= 31:
        raise bad_request("INVALID_TARGET", "OFF 목표는 0~31", "targetCount")
    before = s.off_target
    s.off_target, s.off_target_reason = target, reason
    db.flush()
    audit.log(db, m.ward_id, user_id, "OFF_TARGET_CHANGED", "SCHEDULE", s.id, before, target)
    return _off_target_view(db, s)


# --- 편집 잠금 (SCH-11)
def acquire_lock(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID) -> dict:
    m = members.require_head(db, user_id)
    s = _draft(db, m, schedule_id)
    if _lock_active(s) and s.locked_by != user_id:
        raise conflict("LOCK_ALREADY_HELD", "다른 수간호사가 편집 중입니다", lock_view(db, s))
    return _grant_lock(db, s, user_id)


def release_lock(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID, token: str | None) -> None:
    m = members.require_head(db, user_id)
    s = find(db, m, schedule_id)
    if not _lock_active(s) or s.locked_by != user_id or s.lock_token_hash != sha256(token or ""):
        raise ApiException(403, "LOCK_TOKEN_INVALID", "잠금 토큰이 유효하지 않습니다")
    _unlock(s)


def take_over_lock(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID, reason: str | None) -> dict:
    """강제 인수: 기존 보유자에게 알리고 감사 로그를 남긴다"""
    reason = require_reason(reason)
    m = members.require_head(db, user_id)
    s = _draft(db, m, schedule_id)
    previous = s.locked_by if _lock_active(s) else None
    if previous and previous != user_id:
        notifications.notify_user(db, previous, NotificationType.LOCK_TAKEN_OVER, "편집 잠금 인수",
                                  f"{s.year_month} 근무표 편집권을 {db.get(User, user_id).name}님이 가져갔습니다. "
                                  f"사유: {reason}", "SCHEDULE", s.id)
    audit.log(db, m.ward_id, user_id, "SCHEDULE_LOCK_TAKEN_OVER", "SCHEDULE", s.id, f"user:{previous}",
              f"user:{user_id} reason={reason}")
    return _grant_lock(db, s, user_id)


def editable(db: Session, s: Schedule, m: Member, lock_token: str | None, base_version: int | None) -> None:
    """
    근무표 변경 공통 전제: DRAFT, 잠금(활성 잠금이 있으면 그 토큰 필요), baseVersion 일치.
    잠금이 없으면 baseVersion만으로 충돌을 막는다
    """
    if s.status != ScheduleStatus.DRAFT:
        raise conflict("INVALID_SCHEDULE_STATE", f"초안 상태에서만 편집할 수 있습니다. 현재: {s.status}")
    if _lock_active(s):
        if s.locked_by != m.user_id or s.lock_token_hash != sha256(lock_token or ""):
            raise ApiException(423, "SCHEDULE_LOCKED", "다른 수간호사가 편집 중이거나 잠금 토큰이 없습니다",
                               lock_view(db, s))
        s.lock_activity_at = now()
    if base_version is not None and base_version != s.version:
        raise conflict("VERSION_CONFLICT", "다른 사용자가 먼저 수정했습니다. 새로고침 후 다시 시도하세요",
                       {"currentVersion": s.version})


def lock_view(db: Session, s: Schedule) -> dict | None:
    if not _lock_active(s):
        return None
    return {"holderId": s.locked_by, "holderName": _user_name(db, s.locked_by), "acquiredAt": iso(s.locked_at),
            "lastActivityAt": iso(s.lock_activity_at), "expiresAt": iso(s.lock_activity_at + LOCK_TTL)}


# --- 다른 모듈에서 쓰는 조회·검증
def check(db: Session, s: Schedule, r: Roster | None = None) -> list[dict]:
    """SCH-05 위반 목록 (직렬화). id는 근무표·규칙·간호사·날짜·값으로 정해지는 안정적인 값"""
    r = r or roster(db, s)
    ids = {code: x.id for code, x in rules.rows(db, s.ward_id).items()}
    out = {}
    for v in validator.validate(r):
        vid = uuid.uuid5(s.id, v.key)
        out.setdefault(vid, violation_json(v, vid, ids))
    return list(out.values())


def violation_json(v: Violation, vid: uuid.UUID, rule_ids: dict) -> dict:
    return {"id": vid, "severity": v.severity, "ruleId": rule_ids.get(v.rule_id), "ruleCode": v.rule_id,
            "nurseId": v.nurse_id, "date": iso(v.date), "currentValue": v.current, "message": v.message}


def violation_summary(db: Session, ward_id: uuid.UUID) -> list[dict]:
    """규칙 변경 영향: 초안·확정 근무표별 위반 건수"""
    out = []
    for s in db.scalars(select(Schedule).where(Schedule.ward_id == ward_id,
                                               Schedule.status.in_([ScheduleStatus.DRAFT, ScheduleStatus.CONFIRMED]))
                        .order_by(Schedule.year_month)):
        vs = check(db, s)
        hard = sum(1 for v in vs if v["severity"] == Severity.HARD)
        out.append({"scheduleId": s.id, "yearMonth": s.year_month, "status": s.status, "hardCount": hard,
                    "softCount": len(vs) - hard})
    return out


def violations_for(db: Session, ward_id: uuid.UUID, nurse_id: uuid.UUID) -> list[dict]:
    """NUR-03~06에서 호출. 확정본은 자동 변경하지 않고 위반만 돌려준다"""
    out = []
    for s in db.scalars(select(Schedule).where(Schedule.ward_id == ward_id,
                                               Schedule.status.in_([ScheduleStatus.DRAFT, ScheduleStatus.CONFIRMED]))):
        out += [v for v in check(db, s) if v["nurseId"] == nurse_id]
    return out


def roster(db: Session, s: Schedule) -> Roster:
    """SCH-08: 당시 소속 인원 기준. 퇴사자도 소속 기간이 겹치면 행으로 남는다"""
    first = parse_month(s.year_month)
    last = month_end(first)
    nurses = sorted((n for n in db.scalars(select(Nurse).where(Nurse.ward_id == s.ward_id))
                     if n.affiliation_start <= last and (n.affiliation_end is None or n.affiliation_end >= first)),
                    key=lambda n: (n.name, str(n.id)))
    # 연속 야간·근무를 월 경계 너머로 세기 위해 이전 달 근무표를 함께 넘긴다
    prev = by_month(db, s.ward_id, (first - timedelta(days=1)).replace(day=1))
    return Roster(first, [n.info() for n in nurses], _cells(db, s.id), rules.load(db, s.ward_id),
                  _off_target(db, s), before=_cells(db, prev.id) if prev else {})


def view(db: Session, s: Schedule, m: Member) -> dict:
    """역할에 따라 필드 차등: 임신 여부(nightBlocked)·잠금·확정 가능 여부는 수간호사만, 통계는 본인만"""
    r = roster(db, s)
    holidays = rules.holiday_dates(db, s.ward_id, r.month)
    status = {n.id: n.status for n in r.nurses}
    can_edit = m.is_head and s.status == ScheduleStatus.DRAFT
    cells = []
    for n in r.nurses:
        for d in r.days():
            x = r.cell(n.id, d)
            cells.append({"nurseId": n.id, "date": d.isoformat(), "dutyCode": x,
                          "editable": can_edit and n.affiliated(d) and x != Duty.AL})
    return {"id": s.id, "yearMonth": s.year_month, "status": s.status, "version": s.version,
            "nurses": [{"id": n.id, "name": n.name, "dutyRole": n.duty_role,
                        "affiliationStart": iso(n.affiliation_start), "affiliationEnd": iso(n.affiliation_end),
                        "nightBlocked": status[n.id] == NurseStatus.PREGNANT if m.is_head else None}
                       for n in r.nurses],
            "cells": cells, "coverage": _coverage(r),
            "statistics": [_stat(r, n.id, holidays) for n in r.nurses if m.is_head or n.id == m.nurse_id],
            "confirmedAt": iso(s.confirmed_at), "offTarget": r.off_target,
            "holidays": [d.isoformat() for d in sorted(holidays)],
            "confirmation": _confirmation(db, s),
            "lock": lock_view(db, s) if m.is_head else None,
            "readiness": _readiness(db, s, r) if m.is_head else None}


def find(db: Session, m: Member, schedule_id: uuid.UUID) -> Schedule:
    """일반 간호사에게 DRAFT·GENERATING은 존재하지 않는 것처럼 404"""
    s = db.get(Schedule, schedule_id)
    if s is None or s.ward_id != m.ward_id:
        raise not_found("SCHEDULE_NOT_FOUND")
    return _visible(s, m)


def by_month(db: Session, ward_id: uuid.UUID, first: date) -> Schedule | None:
    return db.scalar(select(Schedule).where(Schedule.ward_id == ward_id,
                                            Schedule.year_month == first.strftime("%Y-%m")))


def touch(db: Session, s: Schedule) -> None:
    """
    내용이 바뀌면 version을 올린다. 읽은 버전일 때만 갱신되므로 동시 요청 중 하나는 409 (낙관적 잠금).
    잠금 획득·해제는 내용이 아니므로 버전을 올리지 않는다
    """
    db.flush()
    n = db.execute(update(Schedule).where(Schedule.id == s.id, Schedule.version == s.version)
                   .values(version=s.version + 1, updated_at=now())
                   .execution_options(synchronize_session=False)).rowcount
    if n != 1:
        raise conflict("VERSION_CONFLICT", "다른 사용자가 먼저 수정했습니다. 새로고침 후 다시 시도하세요")
    set_committed_value(s, "version", s.version + 1)


def _visible(s: Schedule | None, m: Member) -> Schedule:
    if s is None or (not m.is_head and s.status not in VISIBLE_TO_NURSES):
        raise not_found("SCHEDULE_NOT_FOUND")
    return s


def _draft(db: Session, m: Member, schedule_id: uuid.UUID) -> Schedule:
    s = find(db, m, schedule_id)
    if s.status != ScheduleStatus.DRAFT:
        raise conflict("INVALID_SCHEDULE_STATE", f"초안 상태에서만 가능합니다. 현재: {s.status}")
    return s


def _coverage(r: Roster) -> list[dict]:
    out = []
    for d in r.days():
        c = validator.coverage(r, d)
        for duty in (Duty.D, Duty.E, Duty.N):
            have, need = c.get(duty, 0), r.rules.required(duty)
            out.append({"date": d.isoformat(), "dutyCode": duty, "actualCount": have, "requiredCount": need,
                        "status": "UNDER" if have < need else "MET" if have == need else "OVER"})
    return out


def _stat(r: Roster, nurse_id, holidays: set[date]) -> dict:
    c: dict[Duty, int] = {}
    weekend = holiday = 0
    for d in r.days():
        x = r.cell(nurse_id, d)
        if x is None:
            continue
        c[x] = c.get(x, 0) + 1
        if x.is_work and d.weekday() >= 5:
            weekend += 1
        if x.is_work and d in holidays:
            holiday += 1
    return {"nurseId": nurse_id, **{str(k): c.get(k, 0) for k in Duty}, "offTarget": r.off_target,
            "weekendWork": weekend, "holidayWork": holiday}


def _confirmation(db: Session, s: Schedule) -> dict | None:
    """확정·확정 취소 이력. 일반 간호사에게도 보인다 (취소 사유는 알림으로도 공지됨)"""
    if s.confirm_count == 0 and s.cancelled_at is None:
        return None
    return {"confirmedAt": iso(s.confirmed_at), "confirmedBy": _user_name(db, s.confirmed_by),
            "cancelledAt": iso(s.cancelled_at), "cancelledBy": _user_name(db, s.cancelled_by),
            "cancelReason": s.cancel_reason, "confirmCount": s.confirm_count}


def _readiness(db: Session, s: Schedule, r: Roster) -> dict:
    """확정 버튼 활성 판단용. confirmable = 초안 + 하드 위반 0 (소프트 위반은 확인 후 확정)"""
    vs = check(db, s, r)
    hard = sum(1 for v in vs if v["severity"] == Severity.HARD)
    return {"hardViolations": hard, "softViolations": len(vs) - hard,
            "confirmable": s.status == ScheduleStatus.DRAFT and hard == 0}


def _off_target_view(db: Session, s: Schedule) -> dict:
    holidays = len(rules.holiday_dates(db, s.ward_id, parse_month(s.year_month)))
    return {"yearMonth": s.year_month, "targetCount": _off_target(db, s), "autoCount": holidays + 1,
            "holidayCount": holidays, "source": "AUTO" if s.off_target is None else "MANUAL",
            "reason": s.off_target_reason}


def _off_target(db: Session, s: Schedule) -> int:
    """RULE-03: 공휴일 수 + 1. ponytail: 주휴일 포함 여부 🔶 미확정이라 공휴일만 센다"""
    if s.off_target is not None:
        return s.off_target
    return len(rules.holiday_dates(db, s.ward_id, parse_month(s.year_month))) + 1


def _grant_lock(db: Session, s: Schedule, user_id: uuid.UUID) -> dict:
    token = secrets.token_urlsafe(32)
    s.locked_by, s.locked_at, s.lock_activity_at, s.lock_token_hash = user_id, now(), now(), sha256(token)
    db.flush()
    return {"lock": lock_view(db, s), "lockToken": token}


def _unlock(s: Schedule) -> None:
    s.locked_by = s.locked_at = s.lock_activity_at = s.lock_token_hash = None


def _lock_active(s: Schedule) -> bool:
    return s.locked_by is not None and s.lock_activity_at + LOCK_TTL > now()


def _user_name(db: Session, user_id: uuid.UUID | None) -> str | None:
    u = db.get(User, user_id) if user_id else None
    return u.name if u else None


def _assignments(db: Session, schedule_id: uuid.UUID) -> list[Assignment]:
    return list(db.scalars(select(Assignment).where(Assignment.schedule_id == schedule_id)))


def _cells(db: Session, schedule_id: uuid.UUID) -> Cells:
    cells: Cells = {}
    for a in _assignments(db, schedule_id):
        cells.setdefault(a.nurse_id, {})[a.date] = a.duty
    return cells
