"""화면과 공개 근무표에서 공통으로 사용하는 두 가지 정렬."""
from app.domain.model import DutyRole, Role


def proficiency_key(n):
    rank = 0 if n.role == Role.HEAD_NURSE else 1 if n.duty_role == DutyRole.CHARGE else 2
    return rank, -n.career_months, n.name, str(n.id)


def sorted_nurses(rows, sort="proficiency"):
    if sort == "name":
        return sorted(rows, key=lambda n: (n.name, str(n.id)))
    return sorted(rows, key=proficiency_key)
