"""수간호사 대시보드의 월별 그래프용 집계."""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business import members, schedules
from app.business.common import month_end, parse_month
from app.domain.model import Duty, NurseStatus, StaffType
from app.persistence.models import Nurse


def get(db: Session, user_id: uuid.UUID, year_month: str) -> dict:
    member = members.require_head(db, user_id)
    first = parse_month(year_month)
    last = month_end(first)
    personnel = [n for n in db.scalars(select(Nurse).where(Nurse.ward_id == member.ward_id))
                 if n.affiliation_start <= last and (n.affiliation_end is None or n.affiliation_end >= first)]
    composition = [{"staffType": kind, "count": sum(n.staff_type == kind for n in personnel)}
                   for kind in StaffType]
    statuses = [{"status": status, "count": sum(n.status == status for n in personnel)}
                for status in NurseStatus]
    schedule = schedules.by_month(db, member.ward_id, first)
    if schedule is None:
        return {"yearMonth": year_month, "staffComposition": composition, "staffStatuses": statuses,
                "schedule": None, "dutyDistribution": [], "coverageSummary": _coverage_summary([]),
                "dailyCoverage": [], "offSummary": _off_summary([]), "nurseWorkloads": []}

    data = schedules.view(db, schedule, member)
    names = {n["id"]: n for n in data["nurses"]}
    statistics = data["statistics"]
    distribution = [{"dutyCode": duty, "count": sum(row[str(duty)] for row in statistics)} for duty in Duty]
    workloads = [{"nurseId": row["nurseId"], "name": names[row["nurseId"]]["name"],
                  "staffType": names[row["nurseId"]]["staffType"],
                  **{str(duty): row[str(duty)] for duty in Duty},
                  "offTarget": row["offTarget"], "remainingOff": row["remainingOff"],
                  "offCarry": row["offCarry"]} for row in statistics]
    coverage = data["coverage"]
    return {"yearMonth": year_month, "staffComposition": composition, "staffStatuses": statuses,
            "schedule": {"id": data["id"], "status": data["status"], "version": data["version"]},
            "dutyDistribution": distribution, "coverageSummary": _coverage_summary(coverage),
            "dailyCoverage": coverage, "offSummary": _off_summary(statistics),
            "nurseWorkloads": workloads}


def _coverage_summary(rows: list[dict]) -> dict:
    return {status.lower(): sum(row["status"] == status for row in rows) for status in ("UNDER", "MET", "OVER")}


def _off_summary(rows: list[dict]) -> dict:
    return {"target": sum(row["offTarget"] for row in rows), "assigned": sum(row[str(Duty.O)] for row in rows),
            "remaining": sum(row["remainingOff"] for row in rows), "carried": sum(row["offCarry"] for row in rows)}
