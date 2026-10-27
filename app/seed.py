"""로그인 없는 데모를 위한 단일 병동·간호사 28명 시드."""
from datetime import date

from sqlalchemy import select

from app.business import rules
from app.business.common import today_kst
from app.domain.model import DutyRole, NurseRank, NurseStatus, Preset, Role
from app.persistence import models  # noqa: F401
from app.persistence.db import Base, SessionLocal, engine
from app.persistence.models import Nurse, User, Ward
from app.persistence.migrations import migrate_legacy_schema

OPERATOR_EMAIL = "operator@ottugi.local"

# 수간호사 1명, 차지 2명, 일반 25명. 입사 연도와 직급은 독립적이다.
NURSES = [
    ("김수간", NurseRank.HEAD_NURSE, 1998, 5, False),
    ("정차지", NurseRank.CHARGE_NURSE, 2003, 5, False),
    ("문차지", NurseRank.CHARGE_NURSE, 2012, 5, False),
    ("이하늘", NurseRank.STAFF_NURSE, 2001, 5, False),  # 25년 차 일반 간호사
    ("박서연", NurseRank.STAFF_NURSE, 2004, 5, False),
    ("최민준", NurseRank.STAFF_NURSE, 2006, 5, False),
    ("한지민", NurseRank.STAFF_NURSE, 2008, 5, False),
    ("오세훈", NurseRank.STAFF_NURSE, 2010, 4, False),
    ("서유진", NurseRank.STAFF_NURSE, 2012, 4, False),
    ("임도윤", NurseRank.STAFF_NURSE, 2014, 4, False),
    ("배수아", NurseRank.STAFF_NURSE, 2015, 4, False),
    ("조하린", NurseRank.STAFF_NURSE, 2016, 4, False),
    ("장하준", NurseRank.STAFF_NURSE, 2017, 4, False),
    ("송지아", NurseRank.STAFF_NURSE, 2018, 3, False),
    ("백승호", NurseRank.STAFF_NURSE, 2019, 3, False),
    ("강예은", NurseRank.STAFF_NURSE, 2020, 4, True),
    ("권태오", NurseRank.STAFF_NURSE, 2020, 4, True),
    ("황보람", NurseRank.STAFF_NURSE, 2021, 4, True),
    ("신예린", NurseRank.STAFF_NURSE, 2021, 4, True),
    ("노은서", NurseRank.STAFF_NURSE, 2022, 3, False),
    ("하윤아", NurseRank.STAFF_NURSE, 2022, 3, False),
    ("전민재", NurseRank.STAFF_NURSE, 2023, 2, False),
    ("구다은", NurseRank.STAFF_NURSE, 2023, 2, False),
    ("홍시우", NurseRank.STAFF_NURSE, 2024, 2, False),
    ("천소율", NurseRank.STAFF_NURSE, 2024, 1, False),
    ("엄지후", NurseRank.STAFF_NURSE, 2025, 1, False),
    ("류나연", NurseRank.STAFF_NURSE, 2025, 1, False),
    ("윤새봄", NurseRank.STAFF_NURSE, 2026, 1, False),
]


def seed(db) -> None:
    if db.scalar(select(Ward.id)):
        return
    user = db.scalar(select(User).where(User.email == OPERATOR_EMAIL))
    if user is None:
        # 외부 로그인 기능은 없지만 감사 로그의 내부 작업 주체는 안정적인 사용자 ID가 필요하다.
        user = User(email=OPERATOR_EMAIL, password_hash="LOGIN_DISABLED", name="병동 관리자")
        db.add(user)
        db.flush()

    ward = Ward(hospital_name="한빛대학병원", ward_name="7병동", code="DEMO2026")
    db.add(ward)
    db.flush()
    # D/E/N 수치는 클라이언트 확인 전 임시값이며 docs/domain-rules-and-client-qa.md에 기록한다.
    rules.seed(db, ward.id, Preset.STANDARD, {"D": 6, "E": 5, "N": 4})

    start = today_kst().replace(month=1, day=1)
    rows = []
    for index, (name, rank, hire_year, skill, preceptor) in enumerate(NURSES):
        duty_role = (DutyRole.CHARGE if rank == NurseRank.CHARGE_NURSE else
                     DutyRole.PRECEPTOR if preceptor else
                     DutyRole.NEW if skill == 1 else DutyRole.GENERAL)
        nurse = Nurse(
            ward_id=ward.id,
            user_id=user.id if rank == NurseRank.HEAD_NURSE else None,
            name=name,
            role=Role.HEAD_NURSE if rank == NurseRank.HEAD_NURSE else Role.NURSE,
            rank=rank,
            duty_role=duty_role,
            status=NurseStatus.PREGNANT if index == 22 else NurseStatus.ACTIVE,
            hire_year=hire_year,
            is_preceptor=preceptor,
            joined_at=date(hire_year, 1, 1),
            career_months=max(0, (today_kst().year - hire_year) * 12),
            skill_level=skill,
            affiliation_start=start,
            affiliation_end=None,
            preceptor_of=[],
        )
        db.add(nurse)
        rows.append(nurse)
    db.flush()

    beginners = [n for n in rows if n.skill_level == 1]
    for preceptor, beginner in zip((n for n in rows if n.is_preceptor), beginners):
        preceptor.preceptor_of = [str(beginner.id)]


def main() -> None:
    Base.metadata.create_all(engine)
    migrate_legacy_schema(engine)
    with SessionLocal() as db:
        seed(db)
        db.commit()
    print("완료: 수간호사 1명, 차지 2명, 일반 간호사 25명")


if __name__ == "__main__":
    main()
