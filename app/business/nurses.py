"""NUR-01~06"""
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business import audit, members, schedules, wards
from app.business.common import (bad_request, conflict, iso, not_found, page, today_kst, unprocessable)
from app.business.members import Member
from app.domain.model import DutyRole, NurseStatus, Role
from app.domain.nurse_sort import sorted_nurses
from app.persistence.models import Nurse

FIELDS = {"name": "name", "dutyRole": "duty_role", "status": "status", "joinedYear": "joined_year",
          "isPreceptor": "is_preceptor", "careerMonths": "career_months", "skillLevel": "skill_level",
          "affiliationStart": "affiliation_start", "affiliationEnd": "affiliation_end", "preceptorOf": "preceptor_of"}


def list_(db: Session, user_id: uuid.UUID, q: str | None, role: Role | None, duty_role: DutyRole | None,
          status: NurseStatus | None, include_retired: bool, sort: str | None, page_no: int, size: int) -> dict:
    m = members.require(db, user_id)
    if status is not None and not m.is_head:
        # 일반 간호사가 상태 필터로 타인의 임신·휴직 여부를 알아내지 못하게 한다
        raise bad_request("INVALID_FILTER", "상태 필터는 수간호사만 사용할 수 있습니다", "status")
    sort = normalize_sort(sort)
    rows = [n for n in db.scalars(select(Nurse).where(Nurse.ward_id == m.ward_id))
            if (include_retired or status == NurseStatus.RETIRED or n.status != NurseStatus.RETIRED)
            and (not q or q.strip() in n.name)
            and (role is None or n.role == role)
            and (duty_role is None or n.duty_role == duty_role)
            and (status is None or n.status == status)]
    rows = sorted_nurses(rows, sort)
    # ponytail: 병동 단위(수십 명)라 메모리 필터·페이징. 수천 명 규모면 쿼리로
    return page([view(n, _full(m, n)) for n in rows[page_no * size:(page_no + 1) * size]], page_no, size, len(rows))


def get(db: Session, user_id: uuid.UUID, nurse_id: uuid.UUID) -> dict:
    m = members.require(db, user_id)
    n = _get(db, m, nurse_id)
    return view(n, _full(m, n))


def create(db: Session, user_id: uuid.UUID, f: dict) -> dict:
    """계정 없이 간호사를 등록한다. role·병동은 서버에서 정한다."""
    m = members.require_head(db, user_id)
    f = dict(f)
    if db.scalar(select(Nurse.id).where(Nurse.ward_id == m.ward_id, Nurse.name == f["name"],
                                        Nurse.joined_at >= date(f["joined_year"], 1, 1),
                                        Nurse.joined_at <= date(f["joined_year"], 12, 31), Nurse.status != NurseStatus.RETIRED)):
        raise conflict("NURSE_ALREADY_EXISTS", "같은 이름·입사 연도의 간호사가 이미 있습니다")
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
    current = {attr: (n.joined_at.year if attr == "joined_year" else getattr(n, attr)) for attr in FIELDS.values()}
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
    n.invite_code = n.invite_expires_at = None
    unlinked = _unlink_preceptee(db, m, n)
    db.flush()
    audit.log(db, m.ward_id, user_id, "NURSE_RETIRED", "NURSE", n.id, None, end)
    for p, before in unlinked:
        audit.log(db, m.ward_id, user_id, "NURSE_UPDATED", "NURSE", p.id, before, view(p, True))
    return _result(db, m, n)


def _unlink_preceptee(db: Session, m: Member, n: Nurse) -> list[tuple[Nurse, dict]]:
    """퇴사자를 담당 신입으로 가진 프리셉터의 연결만 끊는다. 퇴사자는 담당 신입으로 지정할 수 없기 때문"""
    key, unlinked = str(n.id), []
    for p in db.scalars(select(Nurse).where(Nurse.ward_id == m.ward_id, Nurse.id != n.id)):
        if key in (p.preceptor_of or []):
            before = view(p, True)
            p.preceptor_of = [x for x in p.preceptor_of if x != key]
            unlinked.append((p, before))
    return unlinked


def remove(db: Session, user_id: uuid.UUID, nurse_id: uuid.UUID) -> None:
    """화면의 삭제 동작. 과거 근무표 참조를 보존하기 위해 실제 행은 퇴사 처리한다."""
    m = members.require_head(db, user_id)
    n = _get(db, m, nurse_id)
    end = max(today_kst(), n.affiliation_start)
    if n.affiliation_end is not None and n.affiliation_end < end:
        end = n.affiliation_end  # 이미 정해 둔 더 이른 종료일을 늦추지 않는다
    retire(db, user_id, nurse_id, end)
    # 병동 탈퇴와 같은 정책: 종료일 이후 초안 셀은 지우고, 확정본은 바꾸지 않고 기록만 남긴다
    cleared, confirmed = schedules.clear_after(db, m.ward_id, n.id, end)
    if cleared or confirmed:
        audit.log(db, m.ward_id, user_id, "NURSE_SCHEDULE_CLEARED", "NURSE", n.id, None,
                  f"cleared={cleared} confirmed={confirmed}")


def view(n: Nurse, full: bool) -> dict:
    """관리자 간호사 응답. 입사일 원본은 유지하되 연도만 공개한다."""
    v = {"id": n.id, "name": n.name, "role": n.role, "dutyRole": DutyRole.GENERAL if n.duty_role == DutyRole.PRECEPTOR else n.duty_role,
         "affiliationStart": iso(n.affiliation_start), "affiliationEnd": iso(n.affiliation_end),
         "status": None, "joinedYear": None, "isPreceptor": n.is_preceptor or n.duty_role == DutyRole.PRECEPTOR, "careerMonths": None, "skillLevel": None, "preceptorOf": None,
         "hasAccount": None, "inviteExpiresAt": None, "version": n.version}
    if full:
        v.update(status=n.status, joinedYear=n.joined_at.year, careerMonths=n.career_months, skillLevel=n.skill_level,
                 preceptorOf=sorted(n.preceptor_of or []), hasAccount=n.user_id is not None,
                 inviteExpiresAt=iso(n.invite_expires_at) if n.invite_code else None)
    return v


def _apply(db: Session, m: Member, n: Nurse, f: dict) -> None:
    if f["status"] == NurseStatus.RETIRED:
        raise unprocessable("BUSINESS_RULE_VIOLATION", "퇴사는 퇴사 처리 API를 사용하세요")
    if f["affiliation_end"] and f["affiliation_end"] < f["affiliation_start"]:
        raise bad_request("VALIDATION_ERROR", "소속 종료일이 시작일보다 빠릅니다", "affiliationEnd")
    if f["duty_role"] == DutyRole.PRECEPTOR:
        f = {**f, "duty_role": DutyRole.GENERAL, "is_preceptor": True}
    if f["preceptor_of"] and not f["is_preceptor"]:
        raise unprocessable("INVALID_PRECEPTOR", "프리셉터를 지정한 간호사만 담당 신입을 연결할 수 있습니다")
    duplicate = db.scalar(select(Nurse.id).where(
        Nurse.ward_id == m.ward_id, Nurse.name == f["name"], Nurse.id != n.id,
        Nurse.joined_at >= date(f["joined_year"], 1, 1), Nurse.joined_at <= date(f["joined_year"], 12, 31),
        Nurse.status != NurseStatus.RETIRED))
    if duplicate:
        raise conflict("NURSE_ALREADY_EXISTS", "같은 이름·입사 연도의 간호사가 이미 있습니다")
    for p in f["preceptor_of"] or []:
        target = db.get(Nurse, p)
        if target is None or target.ward_id != m.ward_id:
            raise not_found("NURSE_NOT_FOUND")
        if target.duty_role != DutyRole.NEW or target.status == NurseStatus.RETIRED or p == n.id:
            raise unprocessable("INVALID_PRECEPTEE", "담당 신입은 같은 병동의 NEW 간호사만 지정할 수 있습니다")
    for attr in FIELDS.values():
        if attr == "joined_year":
            if n.joined_at is None or n.joined_at.year != f[attr]:
                n.joined_at = date(f[attr], 1, 1)
        elif attr != "preceptor_of":
            setattr(n, attr, f[attr])
    n.preceptor_of = sorted({str(p) for p in f["preceptor_of"] or []})


def _result(db: Session, m: Member, n: Nurse) -> dict:
    no_charge = not db.scalar(select(Nurse.id).where(Nurse.ward_id == m.ward_id, Nurse.duty_role == DutyRole.CHARGE,
                                                     Nurse.status != NurseStatus.RETIRED))
    return {"nurse": view(n, True), "violations": schedules.violations_for(db, m.ward_id, n.id),
            "warnings": ["병동에 차지 간호사가 없습니다"] if no_charge else []}


def _full(m: Member, n: Nurse) -> bool:
    return m.is_head or n.id == m.nurse_id


def _get(db: Session, m: Member, nurse_id: uuid.UUID) -> Nurse:
    n = db.get(Nurse, nurse_id)
    if n is None or n.ward_id != m.ward_id:
        raise not_found("NURSE_NOT_FOUND")
    return n


def charges(db: Session, user_id: uuid.UUID) -> list[dict]:
    m = members.require_head(db, user_id)
    return [view(n, True) for n in db.scalars(select(Nurse).where(
        Nurse.ward_id == m.ward_id, Nurse.duty_role == DutyRole.CHARGE,
        Nurse.status != NurseStatus.RETIRED).order_by(Nurse.name, Nurse.id))]


def set_charge(db: Session, user_id: uuid.UUID, nurse_id: uuid.UUID, is_charge: bool, version: int) -> dict:
    m = members.require_head(db, user_id)
    n = _get(db, m, nurse_id)
    duty_role = DutyRole.CHARGE if is_charge else (DutyRole.GENERAL if n.duty_role == DutyRole.CHARGE else n.duty_role)
    return patch(db, user_id, nurse_id, {"duty_role": duty_role}, version)


def normalize_sort(sort: str | None) -> str:
    value = sort or "proficiency"
    if value not in ("proficiency", "name"):
        raise bad_request("INVALID_SORT", "정렬은 proficiency 또는 name입니다", "sort")
    return value
