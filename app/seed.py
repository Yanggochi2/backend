"""
로컬 데모 데이터. 실행: uv run python -m app.seed (DATABASE_URL의 DB에 넣는다)
비즈니스 함수를 그대로 거치므로 검증 규칙을 우회하지 않는다. 이미 있는 데이터는 건너뛰어 여러 번 실행해도 안전하다
"""
from datetime import date, timedelta

from sqlalchemy import select

from app.business import auth, generations, members, nurses, requests, schedules, wards
from app.business.common import month_end, today_kst
from app.domain.model import Duty, DutyRole, NurseStatus, Preset, RequestType
from app.persistence import models  # noqa: F401 (테이블 등록)
from app.persistence.db import Base, SessionLocal, engine
from app.persistence.models import Nurse, Schedule, User, WorkRequest

PASSWORD = "Demo1234!"
HEAD = ("김수간", "head@demo.nurs")
# 계정이 있는 일반 간호사 (가입 코드 신청 → 승인)
ACCOUNTS = [("이하늘", "nurse1@demo.nurs"), ("박서연", "nurse2@demo.nurs"), ("최민준", "nurse3@demo.nurs")]
# 계정 없이 수간호사가 등록한 간호사: (이름, 듀티 역할, 경력 개월)
ROSTER = [("정차지", DutyRole.CHARGE, 120), ("강프리", DutyRole.PRECEPTOR, 60), ("윤신입", DutyRole.NEW, 3),
          ("한지민", DutyRole.GENERAL, 36), ("오세훈", DutyRole.GENERAL, 24), ("서유진", DutyRole.GENERAL, 48),
          ("임도윤", DutyRole.GENERAL, 18), ("배수아", DutyRole.GENERAL, 30), ("조하린", DutyRole.GENERAL, 12)]


def _user(db, name: str, email: str) -> User:
    u = db.scalar(select(User).where(User.email == email))
    if u is None:
        auth.signup(db, name, email, PASSWORD, "seed")  # client는 가입 시도 제한 키
        u = db.scalar(select(User).where(User.email == email))
    return u


def _nurse_fields(name: str, duty_role: DutyRole, career: int, start: date) -> dict:
    return {"name": name, "duty_role": duty_role, "status": NurseStatus.ACTIVE, "joined_at": start,
            "career_months": career, "skill_level": min(5, 1 + career // 24), "affiliation_start": start,
            "affiliation_end": None, "preceptor_of": None}


def seed(db) -> None:
    head = _user(db, *HEAD)
    if members.membership(db, head.id) is None:
        wards.create(db, head.id, "한빛대학병원", "7병동", {"D": 2, "E": 2, "N": 2}, Preset.STANDARD)
    ward_id = members.membership(db, head.id).ward_id
    start = today_kst()

    for name, email in ACCOUNTS:
        u = _user(db, name, email)
        if members.membership(db, u.id) is None:
            j = wards.request_join(db, u.id, wards.join_code(db, head.id)["code"])
            wards.approve(db, head.id, j["id"], None)

    existing = set(db.scalars(select(Nurse.name).where(Nurse.ward_id == ward_id)))
    for name, duty_role, career in ROSTER:
        if name not in existing:
            nurses.create(db, head.id, _nurse_fields(name, duty_role, career, start.replace(day=1)))

    # 이번 달: 자동 생성 후 확정 (일반 간호사는 확정된 근무표만 볼 수 있다)
    _schedule(db, head.id, ward_id, start.strftime("%Y-%m"), confirm=True)

    # 다음 달: 신청 → 근무표 생성 → 자동 생성 순서 (승인된 연차가 AL로 미리 채워진다)
    first = month_end(start) + timedelta(days=1)
    ym = first.strftime("%Y-%m")
    n1, n2, n3 = (_user(db, *a) for a in ACCOUNTS)
    if not db.scalar(select(WorkRequest.id).where(WorkRequest.ward_id == ward_id, WorkRequest.year_month == ym)):
        al = requests.create(db, n1.id, RequestType.ANNUAL_LEAVE, [first.replace(day=10), first.replace(day=11)],
                             "FAMILY", None, None)
        requests.approve(db, head.id, al["id"])
        off = requests.create(db, n2.id, RequestType.PREFERRED_OFF, [first.replace(day=5)], "PERSONAL", None, None)
        requests.approve(db, head.id, off["id"])
        # 대기 중으로 남겨 수간호사 승인 화면에서 확인할 수 있게 한다
        requests.create(db, n3.id, RequestType.PREFERRED_SHIFT, [first.replace(day=20)], "STUDY", None, Duty.D)
        requests.create(db, n2.id, RequestType.PREFERRED_OFF, [first.replace(day=15)], "HEALTH", None, None)

    _schedule(db, head.id, ward_id, ym, confirm=False)


def _schedule(db, head_id, ward_id, ym: str, confirm: bool) -> None:
    if db.scalar(select(Schedule.id).where(Schedule.ward_id == ward_id, Schedule.year_month == ym)):
        return
    s = schedules.create(db, head_id, ym)
    job = generations.start(db, head_id, s["id"], [], None, None)
    print(f"{ym} 근무표 자동 생성: {job['status']}")
    if confirm and job["status"] == "SUCCEEDED":
        # 소프트 위반은 수간호사가 확인한 것으로 처리
        soft = schedules.violations(db, head_id, s["id"], None, None, None, 0, 10_000)["data"]
        schedules.confirm(db, head_id, s["id"], {v["id"] for v in soft})
        print(f"{ym} 근무표 확정")


def main() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
        db.commit()
    print(f"완료. 비밀번호 {PASSWORD}  수간호사 {HEAD[1]}  간호사 {', '.join(e for _, e in ACCOUNTS)}")


if __name__ == "__main__":
    main()
