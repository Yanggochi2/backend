# CP-SAT 근무표 생성기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 조사한 간호사 근무표 규칙(하드 8·소프트 6)을 지키는 OR-Tools CP-SAT 생성기로 `app/domain/generator.py`를 교체한다.

**Architecture:** 규칙 목록(CATALOG)에 신규 규칙 5개를 넣고 `Rules`에 월 야간 상한을 추가한다. 검증기가 신규 규칙을 보고하고, 생성기는 같은 규칙을 CP-SAT 제약(하드)과 감점(소프트)으로 모델링한다. 외부 인터페이스(`generate(roster) -> Cells`, 생성 API)는 유지한다.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, OR-Tools CP-SAT (`ortools`), pytest, uv

**Spec:** `docs/superpowers/specs/2026-10-26-cp-sat-generator-design.md`

## Global Constraints

- 하드: COVERAGE, NIGHT_TO_DAY(E→D·N→D·N→E), MAX_CONSECUTIVE_NIGHTS(기본 3), NIGHT_REST(야간 2일 이상 연속 후 OFF 2일), MAX_CONSECUTIVE_WORK(기본 5), PREGNANT_NIGHT, OUT_OF_AFFILIATION, CHARGE_DAY_ONLY, NEW_NIGHT_ALONE, AL·고정 셀 유지
- 소프트 가중치: WISH_OFF_IGNORED 40, MAX_MONTHLY_NIGHTS 50(기본 상한 6), OFF_TARGET 30, NIGHT_OFF_DAY 20, WISH_DUTY_IGNORED 20, WEEKLY_REST 15, ISOLATED_SHIFT 10, PRECEPTOR_MISMATCH 5, 공정성 10
- COVERAGE·NEW_NIGHT_ALONE 여유 변수 가중치 10000, SOFT로 낮춘 하드 규칙 500
- 시간 제한 기본 10초(`GENERATION_DEFAULT_SECONDS`), API 1~60초. 테스트는 1초
- 기존 규칙 코드 이름은 바꾸지 않는다(NIGHT_TO_DAY 유지). 커밋 메시지에 Co-Authored-By를 넣지 않는다

## File Structure

- Modify `pyproject.toml`, `uv.lock` — `ortools` 추가
- Modify `app/domain/model.py` — `Rules.max_monthly_nights`, `RULE_DEFAULTS`(코드 → 기본 심각도) 추가, `Rules.mode()`
- Modify `app/business/rules.py` — CATALOG 신규 5개, NIGHT_TO_DAY 이름, `rows()` 지연 백필, `load()`에 월 야간 상한, MINIMAL에서 NIGHT_REST 끔
- Modify `app/domain/validator.py` — 역순환 3종, NIGHT_REST, MAX_MONTHLY_NIGHTS, NIGHT_OFF_DAY, ISOLATED_SHIFT, WEEKLY_REST
- Create `app/domain/greedy.py` — 기존 그리디 생성기 이동(솔버 실패 시 대체)
- Rewrite `app/domain/generator.py` — CP-SAT 모델
- Modify `app/business/generations.py` — `max_seconds` 전달
- Modify `tests/conftest.py` — `GENERATION_DEFAULT_SECONDS=1`
- Create `tests/test_generator_cpsat.py` — 하드 규칙·품질 테스트
- Modify `tests/test_rules.py` — 넓어진 검증 결과에 맞춤
- Frontend `src/constants/ruleTemplates.ts`, `src/constants/schedule.constants.ts` — 신규 규칙 템플릿, maxSeconds 10

---

### Task 1: 의존성 + 규칙 모델·목록 확장

**Files:** `pyproject.toml`, `app/domain/model.py`, `app/business/rules.py`, Test: `tests/test_rule_api.py`(기존), `tests/test_rules.py`

**Interfaces:**
- Produces: `Rules(..., max_monthly_nights: int = 6, severity=...)`, `Rules.mode(code) -> Severity | None` (꺼짐이면 None, 없으면 `RULE_DEFAULTS[code]`), `RULE_DEFAULTS: dict[str, Severity]`
- Produces: CATALOG 코드 `NIGHT_REST`(HARD), `MAX_MONTHLY_NIGHTS`(SOFT, params `{"max": 6}`), `NIGHT_OFF_DAY`, `ISOLATED_SHIFT`, `WEEKLY_REST`(SOFT)

- [ ] `uv add ortools`
- [ ] `model.py`: `RULE_DEFAULTS` 정의, `Rules`에 `max_monthly_nights: int = 6`(severity 앞), `mode()` 추가, `enforced()`는 `mode(code) == HARD`로
- [ ] `rules.py`: CATALOG 추가, `"NIGHT_TO_DAY": RuleDef("역순환 금지 (E→D, N→D, N→E)", HARD)`, `_MINIMAL_OFF`에 NIGHT_REST, `rows()`가 없는 코드를 `_preset_values(STANDARD, code, staff)`로 만들어 add·flush, `load()`가 `max_monthly_nights=rs["MAX_MONTHLY_NIGHTS"].parameters["max"]` 전달
- [ ] 테스트: 기존 병동에서 규칙 행 하나를 지우고 `GET /wards/me/rules`가 15개를 돌려주는지 (`tests/test_rule_api.py`에 추가)
- [ ] `uv run pytest -q` 통과 후 커밋

### Task 2: 검증기 신규 규칙

**Files:** `app/domain/validator.py`, Test: `tests/test_rules.py`

**Interfaces:** Consumes `Rules.max_monthly_nights`. 위반 rule_id는 CATALOG 코드와 같다. 미배정(None)이 끼면 ISOLATED_SHIFT·WEEKLY_REST는 판단하지 않는다.

- [ ] 실패 테스트: E→D, N→E가 NIGHT_TO_DAY / N N D가 NIGHT_REST / N 7회가 MAX_MONTHLY_NIGHTS / N O D가 NIGHT_OFF_DAY / O D O가 ISOLATED_SHIFT / 연속 OFF 없는 주가 WEEKLY_REST
- [ ] 구현 후 `test_detects_hard_violations` 기대값 갱신(N N D는 NIGHT_REST도 위반)
- [ ] 전체 테스트 통과(시나리오 테스트의 소프트 위반 개수 변화는 원인을 확인해 갱신) 후 커밋

### Task 3: CP-SAT 생성기

**Files:** Create `app/domain/greedy.py`(기존 코드 이동, 함수명 `generate`), Rewrite `app/domain/generator.py`, Modify `app/business/generations.py`, `tests/conftest.py`, Test: `tests/test_generator_cpsat.py`

**Interfaces:** `generate(r: Roster, max_seconds: float = 10) -> Cells`. 반환은 소속 기간 안 모든 날짜에 듀티가 있는 셀(AL·고정 셀 그대로).

모델 요약:
- 칸마다 리터럴: 고정 칸은 파이썬 상수(Duty), 빈 칸은 D/E/N/O BoolVar 4개(합 1). 휴직자는 O 고정, 소속 기간 밖은 칸 없음. 지난달 7일을 상수로 이어 붙인다.
- `_forbid(lits, weight)`: 상수를 접어 넣고, 전부 상수면 건너뛴다(고정 칸끼리 충돌). weight None이면 하드, 아니면 감점 BoolVar.
- 역순환 쌍, 연속 야간(k+1 창), 연속 근무(k+1 창, 근무 리터럴 = O.Not()), 야간 휴식(N,N,D|E 금지 / N,N,¬N,근무 금지), 차지 E·N 금지, 임신 N 금지
- COVERAGE: 날·듀티마다 합 + 부족분 ≥ 필요 인원, 부족분 × 10000. NEW_NIGHT_ALONE: 신입 N ≤ 선배 N 합 + 여유 BoolVar × 10000
- 소프트: 희망 오프(근무 리터럴), 희망 근무(¬해당 듀티), 월 야간 초과, OFF 목표 부족, 나오데 패턴, 고립 패턴 2종, 주별 연속 OFF, 프리셉터 불일치, 공정성(야간·주말 근무·총 근무 max-min)
- 솔버 `max_time_in_seconds=max_seconds`, `num_workers=8`. OPTIMAL/FEASIBLE이 아니면 `greedy.generate(r)`

- [ ] 실패 테스트(작은 근무표, max_seconds=1): 역순환 없음, 야간 2연속 후 OFF 2일, 연속 야간 ≤3, 연속 근무 ≤5, 임신 N 없음, 차지 D만, AL·고정 칸 유지, 지난달 N N 이어받아 1·2일 OFF, 인원 충족
- [ ] 품질 테스트: 30명(차지 3·프리셉터 3·신입 3·임신 1) × 2026-11, D6/E5/N4, max_seconds=10 → 하드 위반 0, 월 야간 > 6인 간호사 0명, 고립 배치 ≤ 간호사 수
- [ ] 구현, `generations.py`에 `DEFAULT_SECONDS = float(os.getenv("GENERATION_DEFAULT_SECONDS", "10"))`와 `generator.generate(..., max_seconds or DEFAULT_SECONDS)`, conftest에 `os.environ.setdefault("GENERATION_DEFAULT_SECONDS", "1")`
- [ ] 전체 테스트 통과 후 커밋

### Task 4: 시드로 실제 결과 확인 + 프론트 규칙 템플릿

**Files:** Frontend `src/constants/ruleTemplates.ts`, `src/constants/schedule.constants.ts`

- [ ] 백엔드: 시드 DB로 11월 생성 결과의 하드 위반 0, 패턴(나오데·고립)이 줄었는지 출력해 확인
- [ ] 프론트: 템플릿에 NIGHT_REST, MAX_MONTHLY_NIGHTS(`max` 1~31, 기본 6), NIGHT_OFF_DAY, ISOLATED_SHIFT, WEEKLY_REST 추가, NIGHT_TO_DAY 이름·문장 변경, `GENERATE_MAX_SECONDS = 10`
- [ ] `npx tsc --noEmit && npx eslint src` 후 커밋·push

### Task 5: push 및 PR

- [ ] 백엔드 `feat/cp-sat-generator` push, base `feat/schedule-flow`로 PR
- [ ] 프론트 PR #28에 push
