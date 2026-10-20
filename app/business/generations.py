"""
GEN-01·02·04 자동 생성 작업.
ponytail: 그리디 생성기가 수 ms라 요청 안에서 동기 실행하고 끝난 작업(202)을 돌려준다. QUEUED·RUNNING·중단은
관찰되지 않는다. 솔버(CP-SAT 등)로 바꿔 수 초를 넘기면 작업 큐 + GENERATING 상태로 전환 (Yanggochi2/backend#25)
"""
import json
import time
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business import audit, members, rules, schedules
from app.business.common import bad_request, conflict, iso, not_found, unprocessable
from app.domain import generator, validator
from app.domain.model import Cells, Duty, GenerationStatus, Severity
from app.persistence.db import now
from app.persistence.models import GenerationJob

MAX_SECONDS = 60  # 서버 설정 상한
RUNNING = (GenerationStatus.QUEUED, GenerationStatus.RUNNING)


def start(db: Session, user_id: uuid.UUID, schedule_id: uuid.UUID, fixed: list[tuple[uuid.UUID, date]],
          max_seconds: int | None, lock_token: str | None) -> dict:
    m = members.require_head(db, user_id)
    s = schedules.find(db, m, schedule_id)
    schedules.editable(db, s, m, lock_token, None)
    if max_seconds is not None and not 1 <= max_seconds <= MAX_SECONDS:
        raise bad_request("VALIDATION_ERROR", f"maxSeconds는 1~{MAX_SECONDS}", "maxSeconds")
    if db.scalar(select(GenerationJob.id).where(GenerationJob.schedule_id == s.id, GenerationJob.status.in_(RUNNING))):
        raise conflict("GENERATION_ALREADY_RUNNING", "이미 자동 생성이 진행 중입니다")
    r = schedules.roster(db, s)
    if not r.nurses:
        raise unprocessable("PRECONDITION_FAILED", "소속 간호사가 없습니다")
    rows = {n.id for n in r.nurses}
    for nurse_id, d in fixed:
        if nurse_id not in rows or d.replace(day=1) != r.month:
            raise unprocessable("PRECONDITION_FAILED", f"이 근무표의 셀이 아닙니다: {nurse_id} {d}")

    started = time.monotonic()
    # 연차(AL)와 고정 셀만 남기고 나머지를 다시 짠다
    keep = set(fixed)
    kept: Cells = {}
    for nurse_id, row in r.cells.items():
        for d, x in row.items():
            if x == Duty.AL or (nurse_id, d) in keep:
                kept.setdefault(nurse_id, {})[d] = x
    cells = generator.generate(r.with_cells(kept))
    after = r.with_cells(cells)
    vs = validator.validate(after)
    hard = [v for v in vs if v.severity == Severity.HARD]
    wishes = [(nurse_id, d) for nurse_id, ds in r.wish_offs.items() for d in ds]
    granted = sum(1 for nurse_id, d in wishes if after.cell(nurse_id, d) in (Duty.O, Duty.AL))

    job = GenerationJob(ward_id=m.ward_id, schedule_id=s.id, created_by=user_id, base_version=s.version,
                        max_seconds=max_seconds or MAX_SECONDS, fixed_cells=[[str(n), d.isoformat()] for n, d in fixed],
                        hard_violation_count=len(hard),
                        metrics={"wishOffRate": granted / len(wishes) if wishes else 1.0,
                                 "softViolationCount": len(vs) - len(hard)})
    if hard:
        # GEN-04: 해 없음. 충돌 원인(남은 하드 위반)·완화안·부분 해를 남기고 근무표는 바꾸지 않는다
        ids = {code: x.id for code, x in rules.rows(db, m.ward_id).items()}
        job.status, job.stage = GenerationStatus.NO_SOLUTION, "DONE"
        job.conflicts = _plain([schedules.violation_json(v, uuid.uuid5(s.id, v.key), ids) for v in hard])
        job.relaxations = [{"id": f"SOFTEN_{code}", "ruleCode": code, "ruleId": str(ids[code]),
                            "description": f"'{rules.CATALOG[code].name}' 규칙을 SOFT로 완화"}
                           for code in sorted({v.rule_id for v in hard}) if rules.relaxable(db, m.ward_id, code)]
        job.partial_cells = [[str(n), d.isoformat(), x] for n, row in cells.items() for d, x in row.items()]
    else:
        job.status, job.stage = GenerationStatus.SUCCEEDED, "DONE"
        schedules.replace_cells(db, s, cells)
    job.finished_at = now()
    db.add(job)
    db.flush()
    job.metrics = {**job.metrics, "elapsedSeconds": round(time.monotonic() - started, 3)}
    audit.log(db, m.ward_id, user_id, "SCHEDULE_GENERATED", "SCHEDULE", s.id, None,
              f"job:{job.id} {job.status} hard={len(hard)}")
    return view(job)


def get(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> dict:
    return view(_job(db, members.require_head(db, user_id), job_id))


def stop(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> dict:
    job = _job(db, members.require_head(db, user_id), job_id)
    if job.status not in RUNNING:
        raise conflict("JOB_ALREADY_FINISHED", f"이미 끝난 작업입니다: {job.status}")
    job.status, job.finished_at = GenerationStatus.STOPPED, now()
    return view(job)


def relax(db: Session, user_id: uuid.UUID, job_id: uuid.UUID, relaxation_ids: list[str],
          lock_token: str | None) -> dict:
    """GEN-04 완화안 적용 후 재실행: 선택한 HARD 규칙을 SOFT로 낮추고(감사 로그) 같은 조건으로 새 작업을 만든다"""
    m = members.require_head(db, user_id)
    job = _job(db, m, job_id)
    offered = {x["id"]: x["ruleCode"] for x in job.relaxations}
    if job.status != GenerationStatus.NO_SOLUTION or not relaxation_ids or any(i not in offered for i in relaxation_ids):
        raise unprocessable("RELAXATION_NOT_ALLOWED", "이 작업에서 제안된 완화안이 아닙니다")
    for i in relaxation_ids:
        rules.soften(db, m, offered[i], f"자동 생성 완화안 (job:{job.id})")
    fixed = [(uuid.UUID(n), date.fromisoformat(d)) for n, d in job.fixed_cells]
    return start(db, user_id, job.schedule_id, fixed, job.max_seconds, lock_token)


def apply_partial(db: Session, user_id: uuid.UUID, job_id: uuid.UUID, base_version: int,
                  lock_token: str | None) -> dict:
    """GEN-04 부분 해 반영. 생성 이후 근무표가 바뀌었으면 409"""
    m = members.require_head(db, user_id)
    job = _job(db, m, job_id)
    if job.partial_cells is None:
        raise not_found("PARTIAL_RESULT_NOT_FOUND")
    s = schedules.find(db, m, job.schedule_id)
    schedules.editable(db, s, m, lock_token, base_version)
    if job.base_version != s.version:
        raise conflict("VERSION_CONFLICT", "생성 이후 근무표가 바뀌었습니다. 다시 생성하세요", {"currentVersion": s.version})
    cells: Cells = {}
    for n, d, x in job.partial_cells:
        cells.setdefault(uuid.UUID(n), {})[date.fromisoformat(d)] = Duty(x)
    schedules.replace_cells(db, s, cells)
    job.partial_cells = None  # 한 번만 반영
    audit.log(db, m.ward_id, user_id, "GENERATION_PARTIAL_APPLIED", "SCHEDULE", s.id, None, f"job:{job.id}")
    return {"schedule": schedules.view(db, s, m), "violations": schedules.check(db, s)}


def view(j: GenerationJob) -> dict:
    return {"id": j.id, "scheduleId": j.schedule_id, "status": j.status, "stage": j.stage,
            "elapsedSeconds": (j.metrics or {}).get("elapsedSeconds", 0),
            "progress": 100 if j.status not in RUNNING else 0, "hardViolationCount": j.hard_violation_count,
            "metrics": j.metrics, "conflicts": j.conflicts, "relaxations": j.relaxations,
            "partialResultAvailable": j.partial_cells is not None, "startedAt": iso(j.started_at),
            "finishedAt": iso(j.finished_at)}


def _job(db: Session, m: members.Member, job_id: uuid.UUID) -> GenerationJob:
    j = db.get(GenerationJob, job_id)
    if j is None or j.ward_id != m.ward_id:
        raise not_found("JOB_NOT_FOUND")
    return j


def _plain(x):
    """JSON 컬럼 저장용: UUID·enum을 문자열로"""
    return json.loads(json.dumps(x, default=str))


