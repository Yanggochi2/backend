"""NUR-01~06"""
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business import audit, members, wards
from app.business.common import (bad_request, conflict, iso, not_found, page, parse_sort, unprocessable)
from app.business.members import Member
from app.domain.model import DutyRole, NurseStatus, Role
from app.persistence.models import Nurse

# 경력·숙련도·입사일 정렬은 그 값을 드러내므로 수간호사만
SORT_ALL, SORT_HEAD = {"name"}, {"name", "careerMonths", "joinedAt", "skillLevel"}
_SORT_KEY = {"name": lambda n: n.name, "careerMonths": lambda n: n.career_months, "joinedAt": lambda n: n.joined_at,
             "skillLevel": lambda n: n.skill_level}
FIELDS = {"name": "name", "dutyRole": "duty_role", "status": "status", "joinedAt": "joined_at",
          "careerMonths": "career_months", "skillLevel": "skill_level", "affiliationStart": "affiliation_start",
          "affiliationEnd": "affiliation_end", "preceptorOf": "preceptor_of"}


def list_(db: Session, user_id: uuid.UUID, q: str | None, role: Role | None, duty_role: DutyRole | None,
          status: NurseStatus | None, include_retired: bool, sort: str | None, page_no: int, size: int) -> dict:
    m = members.require(db, user_id)
    if status is not None and not m.is_head:
        # 일반 간호사가 상태 필터로 타인의 임신·휴직 여부를 알아내지 못하게 한다
        raise bad_request("INVALID_FILTER", "상태 필터는 수간호사만 사용할 수 있습니다", "status")
    field, desc = parse_sort(sort, SORT_HEAD if m.is_head else SORT_ALL, "name,asc")
    rows = [n for n in db.scalars(select(Nurse).where(Nurse.ward_id == m.ward_id))
            if (include_retired or status == NurseStatus.RETIRED or n.status != NurseStatus.RETIRED)
            and (not q or q.strip() in n.name)
            and (role is None or n.role == role)
            and (duty_role is None or n.duty_role == duty_role)
            and (status is None or n.status == status)]
    rows.sort(key=lambda n: (_SORT_KEY[field](n), n.name), reverse=desc)
    # ponytail: 병동 단위(수십 명)라 메모리 필터·페이징. 수천 명 규모면 쿼리로
    return page([view(n, _full(m, n)) for n in rows[page_no * size:(page_no + 1) * size]], page_no, size, len(rows))


def get(db: Session, user_id: uuid.UUID, nurse_id: uuid.UUID) -> dict:
    m = members.require(db, user_id)
    n = _get(db, m, nurse_id)
    return view(n, _full(m, n))


def create(db: Session, user_id: uuid.UUID, f: dict) -> dict:
    """NUR-01. f는 NurseCreate 필드 (snake_case). role은 받지 않는다 (권한은 AUTH-06·07로만)"""
    m = members.require_head(db, user_id)
    if db.scalar(select(Nurse.id).where(Nurse.ward_id == m.ward_id, Nurse.name == f["name"],
                                        Nurse.joined_at == f["joined_at"], Nurse.status != NurseStatus.RETIRED)):
        raise conflict("NURSE_ALREADY_EXISTS", "같은 이름·입사일의 간호사가 이미 있습니다")
    n = Nurse(ward_id=m.ward_id)
    _apply(db, m, n, f)
    db.add(n)
    db.flush()
    audit.log(db, m.ward_id, user_id, "NURSE_REGISTERED", "NURSE", n.id, None, view(n, True))
    return view(n, True)


def patch(db: Session, user_id: uuid.UUID, nurse_id: uuid.UUID, changes: dict, version: int | None) -> dict:
    """NUR-03·04·05. 부분 수정. 영향받는 근무표 위반을 함께 돌려준다"""
    m = members.require_head(db, user_id)
    n = _get(db, m, nurse_id)
    if n.status == NurseStatus.RETIRED:
        raise conflict("INVALID_RESOURCE_STATE", "퇴사자는 수정할 수 없습니다")
    if version is not None and version != n.version:
        raise conflict("VERSION_CONFLICT", "다른 사용자가 먼저 수정했습니다", {"currentVersion": n.version})
    for k, v in changes.items():
        if v is None and k not in ("affiliation_end", "preceptor_of"):
            camel = next(c for c, a in FIELDS.items() if a == k)
            raise bad_request("VALIDATION_ERROR", f"{camel}는 null일 수 없습니다", camel)
    before = view(n, True)
    current = {attr: getattr(n, attr) for attr in FIELDS.values()}
    current["preceptor_of"] = [uuid.UUID(x) for x in n.preceptor_of or []]
    _apply(db, m, n, {**current, **changes})
    db.flush()
    audit.log(db, m.ward_id, user_id, "NURSE_UPDATED", "NURSE", n.id, before, view(n, True))
    return _result(db, m, n)


def retire(db: Session, user_id: uuid.UUID, nurse_id: uuid.UUID, end: date) -> dict:
    """NUR-06. soft delete. 기존 근무표의 행은 소속 기간만큼 남는다"""
    m = members.require_head(db, user_id)
    n = _get(db, m, nurse_id)
    if n.status == NurseStatus.RETIRED:
        raise conflict("INVALID_RESOURCE_STATE", "이미 퇴사 처리되었습니다")
    if n.role == Role.HEAD_NURSE and wards.count_heads(db, m.ward_id) <= 1:
        raise conflict("LAST_HEAD_NURSE", "마지막 수간호사는 권한 이관 후 퇴사할 수 있습니다")
    if end < n.affiliation_start:
        raise unprocessable("INVALID_END_DATE", "소속 종료일이 시작일보다 빠릅니다")
    n.status, n.affiliation_end = NurseStatus.RETIRED, end
    db.flush()
    audit.log(db, m.ward_id, user_id, "NURSE_RETIRED", "NURSE", n.id, None, end)
    return _result(db, m, n)


def view(n: Nurse, full: bool) -> dict:
    """타인의 상태·입사일·경력·숙련도·담당 신입은 수간호사에게만 (1.2). 없는 값은 null"""
    v = {"id": n.id, "name": n.name, "role": n.role, "dutyRole": n.duty_role,
         "affiliationStart": iso(n.affiliation_start), "affiliationEnd": iso(n.affiliation_end),
         "status": None, "joinedAt": None, "careerMonths": None, "skillLevel": None, "preceptorOf": None,
         "hasAccount": None, "version": n.version}
    if full:
        v.update(status=n.status, joinedAt=iso(n.joined_at), careerMonths=n.career_months, skillLevel=n.skill_level,
                 preceptorOf=sorted(n.preceptor_of or []), hasAccount=n.user_id is not None)
    return v


def _apply(db: Session, m: Member, n: Nurse, f: dict) -> None:
    if f["status"] == NurseStatus.RETIRED:
        raise unprocessable("BUSINESS_RULE_VIOLATION", "퇴사는 퇴사 처리 API를 사용하세요")
    if f["affiliation_end"] and f["affiliation_end"] < f["affiliation_start"]:
        raise bad_request("VALIDATION_ERROR", "소속 종료일이 시작일보다 빠릅니다", "affiliationEnd")
    for p in f["preceptor_of"] or []:
        target = db.get(Nurse, p)
        if target is None or target.ward_id != m.ward_id:
            raise not_found("NURSE_NOT_FOUND")
        if target.duty_role != DutyRole.NEW or p == n.id:
            raise unprocessable("INVALID_PRECEPTEE", "담당 신입은 같은 병동의 NEW 간호사만 지정할 수 있습니다")
    for attr in FIELDS.values():
        if attr != "preceptor_of":
            setattr(n, attr, f[attr])
    n.preceptor_of = sorted({str(p) for p in f["preceptor_of"] or []})


def _result(db: Session, m: Member, n: Nurse) -> dict:
    no_charge = not db.scalar(select(Nurse.id).where(Nurse.ward_id == m.ward_id, Nurse.duty_role == DutyRole.CHARGE,
                                                     Nurse.status != NurseStatus.RETIRED))
    return {"nurse": view(n, True), "violations": [],  # 영향받는 근무표 위반은 근무표 기능에서 채운다
            "warnings": ["병동에 차지 간호사가 없습니다"] if no_charge else []}


def _full(m: Member, n: Nurse) -> bool:
    return m.is_head or n.id == m.nurse_id


def _get(db: Session, m: Member, nurse_id: uuid.UUID) -> Nurse:
    n = db.get(Nurse, nurse_id)
    if n is None or n.ward_id != m.ward_id:
        raise not_found("NURSE_NOT_FOUND")
    return n
