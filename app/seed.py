"""독립된 28명 데모 병동. python -m app.seed. 실제 병동의 인원/근무표는 변경하지 않는다."""
from sqlalchemy import select

from app.business import access, members, nurses, schedules
from app.business.common import today_kst
from app.domain.model import DutyRole, NurseStatus, Preset
from app.persistence.db import Base, SessionLocal, engine
from app.persistence.migrations import upgrade
from app.persistence.models import Nurse, Ward

DEMO_WARD = "10/26 데모 병동"
# 일반 간호사 25명. 프리셉터는 별도 속성이고 고경력 일반 간호사가 함께 있다.
GENERAL = [
    ("강하늘", 300, False), ("권태호", 60, True), ("김수관", 72, True),
    ("노은서", 3, False), ("도유진", 240, False), ("류서연", 180, False),
    ("문지호", 144, False), ("박수아", 120, False), ("배지민", 108, False),
    ("서하린", 96, False), ("성민재", 84, False), ("송가람", 66, False),
    ("신예린", 54, False), ("안도윤", 48, False), ("오세훈", 42, False),
    ("유채원", 36, False), ("윤지아", 30, False), ("이하준", 24, False),
    ("임보람", 18, False), ("장나연", 15, False), ("전승호", 12, False),
    ("정소율", 9, False), ("조다은", 6, False), ("최시우", 4, False),
    ("한지후", 2, False),
]


def seed(db):
    today = today_kst()
    first = today.replace(day=1)
    ward = db.scalar(select(Ward).where(Ward.ward_name == DEMO_WARD, Ward.hospital_name == "데모 병원"))
    if ward is None:
        result = access.create(db, dict(hospital_name="데모 병원", ward_name=DEMO_WARD, head_name="홍수간",
            joined_year=today.year - 26, career_months=312, affiliation_start=first,
            required_staff={"D": 7, "E": 5, "N": 4}, rule_preset=Preset.STANDARD))
        ward_id = result["ward"]["id"]
    else:
        ward_id = ward.id
        result = {"ward": {"id": ward_id}, **access.issue(db, ward_id)}
    actor = db.scalar(select(Nurse).where(Nurse.ward_id == ward_id, Nurse.role == "HEAD_NURSE")).user_id
    existing = {n.name: n for n in db.scalars(select(Nurse).where(Nurse.ward_id == ward_id))}
    rows = [("김차지", 276, False, DutyRole.CHARGE), ("이차지", 120, False, DutyRole.CHARGE)]
    rows += [(name, months, preceptor, DutyRole.NEW if months < 12 else DutyRole.GENERAL)
             for name, months, preceptor in GENERAL]
    for name, months, preceptor, role in rows:
        if name not in existing:
            result_nurse = nurses.create(db, actor, dict(name=name, duty_role=role, is_preceptor=preceptor,
                status=NurseStatus.ACTIVE, joined_year=today.year - months // 12, career_months=months,
                skill_level=min(5, 1 + months // 24), affiliation_start=first, affiliation_end=None, preceptor_of=None))
            existing[name] = db.get(Nurse, result_nurse["id"])
    for preceptor, trainee in (("권태호", "노은서"), ("김수관", "한지후")):
        if not existing[preceptor].preceptor_of:
            nurses.patch(db, actor, existing[preceptor].id, {"preceptor_of": [existing[trainee].id]}, None)
    if schedules.by_month(db, ward_id, first) is None:
        schedules.create(db, actor, first.strftime("%Y-%m"))
    return result


def main():
    import json
    Base.metadata.create_all(engine)
    upgrade(engine)
    with SessionLocal() as db:
        result = seed(db)
        db.commit()
        print(json.dumps(result, ensure_ascii=False, default=str))
    print("28명 데모 생성 완료. 기존 데모에 재실행하면 관리 링크를 교체합니다. 생성·확정은 관리 화면에서 수행하세요.")


if __name__ == "__main__":
    main()
