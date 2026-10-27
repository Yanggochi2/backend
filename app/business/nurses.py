"""간호사 관리. 직급·경력·프리셉터 역할은 서로 독립된 값으로 저장한다."""
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business import audit, members, schedules
from app.business.common import bad_request, conflict, iso, not_found, page, parse_sort, today_kst, unprocessable
from app.business.members import Member
from app.domain.model import DutyRole, NurseRank, NurseStatus, Role
from app.persistence.models import Nurse

SORTS = {"name", "hireYear", "skillLevel", "rank"}
_SORT_KEY = {"name": lambda n: n.name, "hireYear": lambda n: n.hire_year,
             "skillLevel": lambda n: n.skill_level, "rank": lambda n: n.rank.value}
FIELDS = {"name": "name", "rank": "rank", "hireYear": "hire_year", "isPreceptor": "is_preceptor",
          "careerMonths": "career_months", "status": "status", "skillLevel": "skill_level",
          "affiliationStart": "affiliation_start",
          "affiliationEnd": "affiliation_end", "preceptorOf": "preceptor_of"}


def list_(db: Session, user_id: uuid.UUID, q: str | None, rank: NurseRank | None, role: Role | None,
          duty_role: DutyRole | None, status: NurseStatus | None, include_retired: bool,
          sort: str | None, page_no: int, size: int) -> dict:
    m = members.require_head(db, user_id)
    field, desc = parse_sort(sort, SORTS, "name,asc")
    rows = [n for n in db.scalars(select(Nurse).where(Nurse.ward_id == m.ward_id))
            if (include_retired or status == NurseStatus.RETIRED or n.status != NurseStatus.RETIRED)
            and (not q or q.strip() in n.name)
            and (rank is None or n.rank == rank)
            and (role is None or n.role == role)
            and (duty_role is None or n.duty_role == duty_role)
            and (status is None or n.status == status)]
    rows.sort(key=lambda n: (_SORT_KEY[field](n), n.name), reverse=desc)
    return page([view(n) for n in rows[page_no * size:(page_no + 1) * size]], page_no, size, len(rows))


def get(db: Session, user_id: uuid.UUID, nurse_id: uuid.UUID) -> dict:
    return view(_get(db, members.require_head(db, user_id), nurse_id))


def create(db: Session, user_id: uuid.UUID, f: dict) -> dict:
    m = members.require_head(db, user_id)
    if db.scalar(select(Nurse.id).where(Nurse.ward_id == m.ward_id, Nurse.name == f["name"],
                                        Nurse.hire_year == f["hire_year"], Nurse.status != NurseStatus.RETIRED)):
        raise conflict("NURSE_ALREADY_EXISTS", "같은 이름·입사 연도의 간호사가 이미 있습니다")
    n = Nurse(ward_id=m.ward_id, user_id=None)
    _apply(db, m, n, f)
    db.add(n)
    db.flush()
    audit.log(db, m.ward_id, user_id, "NURSE_REGISTERED", "NURSE", n.id, None, view(n))
    return view(n)


def patch(db: Session, user_id: uuid.UUID, nurse_id: uuid.UUID, changes: dict, version: int | None) -> dict:
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
    before = view(n)
    current = {attr: getattr(n, attr) for attr in FIELDS.values()}
    current["preceptor_of"] = [uuid.UUID(x) for x in n.preceptor_of or []]
    current["duty_role"] = n.duty_role
    _apply(db, m, n, {**current, **changes})
    db.flush()
    audit.log(db, m.ward_id, user_id, "NURSE_UPDATED", "NURSE", n.id, before, view(n))
    return _result(db, m, n)


def retire(db: Session, user_id: uuid.UUID, nurse_id: uuid.UUID, end: date) -> dict:
    m = members.require_head(db, user_id)
    n = _get(db, m, nurse_id)
    if n.status == NurseStatus.RETIRED:
        raise conflict("INVALID_RESOURCE_STATE", "이미 퇴사 처리되었습니다")
    if n.rank == NurseRank.HEAD_NURSE:
        raise conflict("HEAD_NURSE_REQUIRED", "수간호사 1명은 반드시 유지해야 합니다")
    if end < n.affiliation_start:
        raise unprocessable("INVALID_END_DATE", "소속 종료일이 시작일보다 빠릅니다")
    n.status, n.affiliation_end = NurseStatus.RETIRED, end
    n.invite_code = n.invite_expires_at = None
    db.flush()
    audit.log(db, m.ward_id, user_id, "NURSE_RETIRED", "NURSE", n.id, None, end)
    return _result(db, m, n)


def view(n: Nurse) -> dict:
    return {"id": n.id, "name": n.name, "rank": n.rank, "hireYear": n.hire_year,
            "isPreceptor": n.is_preceptor, "status": n.status, "skillLevel": n.skill_level,
            "role": n.role, "dutyRole": n.duty_role, "joinedAt": iso(n.joined_at),
            "careerMonths": n.career_months,
            "affiliationStart": iso(n.affiliation_start), "affiliationEnd": iso(n.affiliation_end),
            "preceptorOf": sorted(n.preceptor_of or []), "version": n.version}


def _apply(db: Session, m: Member, n: Nurse, f: dict) -> None:
    year = today_kst().year
    if f["status"] == NurseStatus.RETIRED:
        raise unprocessable("BUSINESS_RULE_VIOLATION", "퇴사는 퇴사 처리 API를 사용하세요")
    if f["hire_year"] > year:
        raise bad_request("VALIDATION_ERROR", "입사 연도는 현재 연도보다 늦을 수 없습니다", "hireYear")
    if f["affiliation_end"] and f["affiliation_end"] < f["affiliation_start"]:
        raise bad_request("VALIDATION_ERROR", "소속 종료일이 시작일보다 빠릅니다", "affiliationEnd")
    if f["is_preceptor"] and (f["rank"] != NurseRank.STAFF_NURSE or year - f["hire_year"] not in (5, 6)):
        raise unprocessable("INVALID_PRECEPTOR",
                            "프리셉터는 일반 간호사 중 입사 후 5~6년인 간호사만 지정할 수 있습니다")
    if f["rank"] == NurseRank.HEAD_NURSE:
        other = db.scalar(select(Nurse.id).where(Nurse.ward_id == m.ward_id,
                                                 Nurse.rank == NurseRank.HEAD_NURSE,
                                                 Nurse.status != NurseStatus.RETIRED,
                                                 Nurse.id != n.id))
        if other:
            raise conflict("HEAD_NURSE_ALREADY_EXISTS", "수간호사는 병동에 1명만 둘 수 있습니다")
    elif n.id is not None and n.rank == NurseRank.HEAD_NURSE:
        raise conflict("HEAD_NURSE_REQUIRED", "수간호사 1명은 반드시 유지해야 합니다")
    for p in f["preceptor_of"] or []:
        target = db.get(Nurse, p)
        if target is None or target.ward_id != m.ward_id:
            raise not_found("NURSE_NOT_FOUND")
        if target.skill_level != 1 or p == n.id:
            raise unprocessable("INVALID_PRECEPTEE", "담당 간호사는 같은 병동의 숙련도 1 간호사만 지정할 수 있습니다")
    for attr in FIELDS.values():
        if attr != "preceptor_of":
            setattr(n, attr, f[attr])
    n.preceptor_of = sorted({str(p) for p in f["preceptor_of"] or []})
    n.role = Role.HEAD_NURSE if n.rank == NurseRank.HEAD_NURSE else Role.NURSE
    n.joined_at = date(n.hire_year, 1, 1)
    if n.career_months is None:
        n.career_months = max(0, (year - n.hire_year) * 12)
    legacy_duty_role = f.get("duty_role")
    n.duty_role = (DutyRole.CHARGE if n.rank == NurseRank.CHARGE_NURSE else
                   DutyRole.PRECEPTOR if n.is_preceptor else
                   legacy_duty_role if legacy_duty_role in (DutyRole.GENERAL, DutyRole.NEW, DutyRole.NIGHT_ONLY) else
                   DutyRole.NEW if n.skill_level == 1 else DutyRole.GENERAL)


def _result(db: Session, m: Member, n: Nurse) -> dict:
    charge_count = len(db.scalars(select(Nurse.id).where(
        Nurse.ward_id == m.ward_id, Nurse.rank == NurseRank.CHARGE_NURSE,
        Nurse.status != NurseStatus.RETIRED)).all())
    warnings = [] if charge_count == 2 else [f"차지 간호사는 2명이 필요합니다 (현재 {charge_count}명)"]
    return {"nurse": view(n), "violations": schedules.violations_for(db, m.ward_id, n.id), "warnings": warnings}


def _get(db: Session, m: Member, nurse_id: uuid.UUID) -> Nurse:
    n = db.get(Nurse, nurse_id)
    if n is None or n.ward_id != m.ward_id:
        raise not_found("NURSE_NOT_FOUND")
    return n


def charges(db: Session, user_id: uuid.UUID) -> list[dict]:
    m = members.require_head(db, user_id)
    return [view(n) for n in db.scalars(select(Nurse).where(
        Nurse.ward_id == m.ward_id, Nurse.rank == NurseRank.CHARGE_NURSE,
        Nurse.status != NurseStatus.RETIRED).order_by(Nurse.name, Nurse.id))]


def set_charge(db: Session, user_id: uuid.UUID, nurse_id: uuid.UUID, is_charge: bool, version: int) -> dict:
    rank = NurseRank.CHARGE_NURSE if is_charge else NurseRank.STAFF_NURSE
    return patch(db, user_id, nurse_id, {"rank": rank}, version)
