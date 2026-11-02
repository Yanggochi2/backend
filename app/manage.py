"""기존 병동 전환 CLI. python -m app.manage issue-link <ward UUID>"""
import argparse
import json
import uuid

from app.business import access, audit, rules
from app.domain.model import Preset
from app.persistence.db import Base, SessionLocal, engine
from app.persistence.migrations import upgrade
from app.persistence.models import Ward


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["issue-link", "apply-client-rules"])
    parser.add_argument("ward_id", type=uuid.UUID)
    args = parser.parse_args()
    Base.metadata.create_all(engine)
    upgrade(engine)
    with SessionLocal() as db:
        if db.get(Ward, args.ward_id) is None:
            parser.error("병동을 찾을 수 없습니다")
        if args.action == "issue-link":
            result = access.issue(db, args.ward_id)
        else:
            rows = rules.rows(db, args.ward_id)
            codes = ("CHARGE_DAY_ONLY", "CHARGE_EVERY_SHIFT", "SENIOR_EVERY_SHIFT", "OFF_TARGET", "WEEKDAY_DAY_ONLY")
            for code in codes:
                row = rows[code]
                before = rules.view(row)
                for key, value in rules._preset_values(Preset.STANDARD, code, {}).items():
                    setattr(row, key, value)
                audit.log(db, args.ward_id, None, "CLIENT_1026_RULE_MIGRATED", "RULE", code, before, rules.view(row))
            result = {"updatedRules": codes, "note": "확정된 근무표는 변경하지 않습니다. 초안에서 재검증하세요."}
        db.commit()
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
