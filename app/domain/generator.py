"""
GEN-01. OR-Tools CP-SAT으로 근무표를 편성한다. 규칙 기준: docs/client-1026-plan.md
- 입력 셀(연차 AL, GEN-05 고정 셀)은 그대로 두고 빈 칸만 채운다. 휴직자는 O, 소속 기간 밖은 칸이 없다
- HARD로 켜진 규칙은 제약으로, SOFT 규칙과 SOFT로 낮춘 하드 규칙은 감점으로 최적화한다
- 인원 충족(COVERAGE)·신입 단독 야간 금지는 아주 큰 감점으로 둔다. 인원이 부족한 달에도 결과를 내고,
  남은 하드 위반은 검증기가 잡아 해 없음(GEN-04) 흐름으로 간다
- 고정 칸끼리만으로 이미 어긋나는 구간(지난달 말과 이어지는 구간 등)은 제약에서 뺀다. 그 위반은 검증기가 보고한다
"""
import os
import time
from datetime import date, timedelta

from ortools.sat.python import cp_model

from app.domain import greedy
from app.domain.model import (NIGHT_ONLY_MAX_NIGHTS, SENIOR_ROLES, Cells, Duty, DutyRole, NurseStatus, Roster,
                              Severity, StaffType)
from app.domain.validator import SKILL_TOLERANCE, has_role, off_target

SHIFTS = (Duty.D, Duty.E, Duty.N, Duty.O)
COVERAGE_WEIGHT = 10_000
NEW_ALONE_WEIGHT = 20_000  # 신입 혼자 야간보다 인원 부족으로 남기는 편이 낫다
SOFTENED_WEIGHT = 500      # SOFT로 낮춘 하드 규칙
# OFF 개수는 간호사 사이 가장 중요한 공정성 기준이라 하드 규칙 수준(1단계)으로 맞춘다
WEIGHTS = {"WISH_OFF_IGNORED": 40, "MAX_MONTHLY_NIGHTS": 50, "OFF_TARGET": SOFTENED_WEIGHT, "NIGHT_OFF_DAY": 20,
           "WISH_DUTY_IGNORED": 20, "WEEKLY_REST": 15, "ISOLATED_SHIFT": 10, "SHIFT_BLOCK": 5,
           "PRECEPTOR_MISMATCH": 5, "SKILL_MIX": 5}
SKILL_OUT_WEIGHT = 100  # 근무 평균 숙련도가 허용 범위를 벗어난 정도
FAIRNESS_WEIGHT = 10
# 필요 인원을 넘는 인력은 D에 먼저, 그다음 E에 둔다 (N은 필요 인원만). 일당 감점
OFF_EXACT_WEIGHT = 30  # 허용 편차 안에서도 목표에 가깝게
REVERSE = ((Duty.E, Duty.D), (Duty.N, Duty.D), (Duty.N, Duty.E))  # 근무 사이 휴식이 11시간 미만
LOOKBACK_DAYS = 7  # 지난달 말 근무를 이어서 볼 일수 (연속 근무 최대 5일 + 여유)
WORK, REST = "WORK", "REST"
SKIP = object()  # 꺼진 규칙
# 탐색 시간 기본값(초). 테스트는 짧게 둔다
DEFAULT_SECONDS = float(os.getenv("GENERATION_DEFAULT_SECONDS", "30"))


FOUND = (cp_model.OPTIMAL, cp_model.FEASIBLE)


def generate(r: Roster, max_seconds: float | None = None) -> Cells:
    """
    두 단계로 푼다. 1단계(시간의 30%)는 하드 규칙(큰 감점)만 줄여 위반 없는 해를 빨리 찾고,
    2단계는 그 해를 시작점으로 소프트 규칙까지 최적화한다. 한 번에 풀면 소프트 감점에 묻혀 하드 위반이 늦게 사라진다
    """
    total = max_seconds or DEFAULT_SECONDS
    model = _Model(r)
    model.build()
    # 솔버가 해를 못 찾으면 그리디 결과를 쓴다. 시작점은 달력과 O 목표를 반영한 힌트
    best = greedy.generate(r)
    model.hint(_rotation_hint(r))
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 8
    deadline = time.monotonic() + total
    for minimum, limit in ((SOFTENED_WEIGHT, total * 0.3), (0, None)):
        model.m.Minimize(sum(w * e for w, e in model.cost if w >= minimum))
        # 1단계가 일찍 끝나면 남은 시간을 모두 2단계에 쓴다
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        solver.parameters.max_time_in_seconds = min(limit or remaining, remaining)
        if solver.Solve(model.m) in FOUND:
            best = model.cells(solver)
            model.hint(best)
    return best


def _rotation_hint(r: Roster) -> Cells:
    """기존 셀·주5일제와 O 목표를 반영한 시작점. 최종 편성은 솔버가 결정한다."""
    out: Cells = {}
    for k, n in enumerate(r.nurses):
        row = {}
        eligible = [d for d in r.days() if n.affiliated(d)]
        target = off_target(r, n)
        for i, d in enumerate(eligible):
            if r.cell(n.id, d) is not None:
                row[d] = r.cell(n.id, d)
            elif n.status == NurseStatus.ON_LEAVE:
                row[d] = Duty.O
            elif n.weekday_only:
                row[d] = Duty.O if d.weekday() >= 5 or d in r.holidays else Duty.D
            elif (i + k * 3) % len(eligible) < target:
                row[d] = Duty.O
            else:
                row[d] = (Duty.D, Duty.E, Duty.N)[(i // 3 + k) % 3]
        out[n.id] = row
    return out


def _neg(lit):
    return (not lit) if isinstance(lit, bool) else lit.Not()


class _Model:
    def __init__(self, r: Roster):
        self.r, self.rules, self.m = r, r.rules, cp_model.CpModel()
        self.days = r.days()
        self.timeline = [self.days[0] - timedelta(days=k) for k in range(LOOKBACK_DAYS, 0, -1)] + self.days
        self.cost: list = []  # (가중치, 선형식)
        self.free: dict = {}  # (간호사 id, 날짜) -> {Duty: BoolVar}
        for n in r.nurses:
            for d in self.days:
                if n.affiliated(d) and r.cell(n.id, d) is None and n.status != NurseStatus.ON_LEAVE:
                    vs = {s: self.m.NewBoolVar(f"{n.id}_{d}_{s}") for s in SHIFTS}
                    self.m.AddExactlyOne(vs.values())
                    self.free[n.id, d] = vs

    # --- 칸 읽기 ---
    def const(self, n, d: date) -> Duty | None:
        """고정된 칸의 값. 휴직자의 빈 칸은 O, 소속 기간 밖·미배정은 None"""
        x = self.r.cell(n.id, d)
        if x is None and d >= self.days[0] and n.affiliated(d) and n.status == NurseStatus.ON_LEAVE:
            return Duty.O
        return x

    def known(self, n, d: date) -> bool:
        return (n.id, d) in self.free or self.const(n, d) is not None

    def lit(self, n, d: date, kind):
        """칸이 kind(D·E·N·O, WORK, REST)인지: 빈 칸이면 BoolVar, 고정 칸이면 bool"""
        vs = self.free.get((n.id, d))
        if vs is not None:
            return vs[Duty.O].Not() if kind == WORK else vs[Duty.O] if kind == REST else vs.get(kind, False)
        x = self.const(n, d)
        work = x is not None and x.is_work
        return work if kind == WORK else not work if kind == REST else x == kind

    def count(self, n, days, kind):
        """days 중 kind인 칸 수 (선형식)"""
        return sum(self.lit(n, d, kind) * 1 for d in days)

    # --- 제약 도구 ---
    def weight(self, code: str, hard_weight=None):
        """꺼짐이면 SKIP, HARD면 hard_weight(None이면 제약), SOFT로 낮춘 하드 규칙은 SOFTENED_WEIGHT"""
        if not self.rules.enabled(code):
            return SKIP
        mode = self.rules.severity_for(code)
        if code in WEIGHTS:
            return None if mode == Severity.HARD else WEIGHTS[code]
        return hard_weight if mode == Severity.HARD else SOFTENED_WEIGHT

    def forbid(self, lits, weight):
        """lits가 모두 참인 상황을 막는다(weight None) 또는 감점한다. 고정 칸만으로 정해진 경우는 건너뛴다"""
        if weight is SKIP or any(x is False for x in lits):
            return
        rest = [x for x in lits if x is not True]
        if not rest:
            return
        clause = [x.Not() for x in rest]
        if weight is None:
            self.m.AddBoolOr(clause)
        else:
            p = self.m.NewBoolVar("")
            self.m.AddBoolOr(clause + [p])
            self.cost.append((weight, p))

    def at_least(self, expr, need: int, weight):
        """expr >= need. 모자란 만큼 weight로 감점"""
        if weight is SKIP or need <= 0:
            return
        if weight is None:
            self.m.Add(expr >= need)
            return
        short = self.m.NewIntVar(0, need, "")
        self.m.Add(expr + short >= need)
        self.cost.append((weight, short))

    def at_most(self, expr, limit: int, weight, upper: int):
        if weight is SKIP:
            return
        if weight is None:
            self.m.Add(expr <= limit)
            return
        over = self.m.NewIntVar(0, upper, "")
        self.m.Add(expr - over <= limit)
        self.cost.append((weight, over))

    # --- 모델 ---
    def build(self):
        for n in self.r.nurses:
            self._nurse_hard(n)
            self._nurse_soft(n)
        self._coverage()
        self._fairness()

    def _nurse_hard(self, n):
        rules, t = self.rules, self.timeline
        w = self.weight("NIGHT_TO_DAY")
        if w is not SKIP:
            for a, b in zip(t, t[1:]):
                for x, y in REVERSE:
                    self.forbid([self.lit(n, a, x), self.lit(n, b, y)], w)
        for d in self.days:
            if self.r.cell(n.id, d) == Duty.ED:
                for x in (Duty.E, Duty.N):
                    self.forbid([self.lit(n, d - timedelta(days=1), x)], self.weight("NIGHT_TO_DAY"))
        w = self.weight("MAX_CONSECUTIVE_NIGHTS")
        k = rules.parameter("MAX_CONSECUTIVE_NIGHTS", "max")
        for i in range(len(t) - k):
            self.forbid([self.lit(n, d, Duty.N) for d in t[i:i + k + 1]], w)
        w = self.weight("MAX_CONSECUTIVE_WORK")
        k = rules.parameter("MAX_CONSECUTIVE_WORK", "max")
        for i in range(len(t) - k):
            self.forbid([self.lit(n, d, WORK) for d in t[i:i + k + 1]], w)
        w = self.weight("NIGHT_REST")
        k = rules.parameter("NIGHT_REST", "nights")
        for i in range(k - 1, len(t) - 1):
            nn = [self.lit(n, d, Duty.N) for d in t[i - k + 1:i + 1]]
            for x in (Duty.D, Duty.E, Duty.ED):
                self.forbid(nn + [self.lit(n, t[i + 1], x)], w)
            if i + 2 < len(t):
                self.forbid(nn + [_neg(self.lit(n, t[i + 1], Duty.N)), self.lit(n, t[i + 2], WORK)], w)
        w = self.weight("PING_PONG")
        if w is not SKIP:
            for i in range(2, len(t)):
                if not all(self.known(n, d) for d in t[i - 2:i + 1]):
                    continue
                # 매일 다른 근무 (D E N 등)
                for a in (Duty.D, Duty.E, Duty.N):
                    for b in (Duty.D, Duty.E, Duty.N):
                        for c in (Duty.D, Duty.E, Duty.N):
                            if a != b != c:
                                self.forbid([self.lit(n, t[i - 2], a), self.lit(n, t[i - 1], b),
                                             self.lit(n, t[i], c)], w)
                # 근무·휴무 4일 교대 (W R W R, R W R W)
                if i >= 3 and self.known(n, t[i - 3]):
                    for kinds in ((WORK, REST, WORK, REST), (REST, WORK, REST, WORK)):
                        self.forbid([self.lit(n, d, x) for d, x in zip(t[i - 3:i + 1], kinds)], w)
        for d in self.days:
            if n.weekday_only and n.status != NurseStatus.ON_LEAVE and (n.id, d) in self.free:
                expected = Duty.O if d.weekday() >= 5 or d in self.r.holidays else Duty.D
                self.forbid([_neg(self.lit(n, d, expected))], self.weight("WEEKDAY_DAY_ONLY"))
            if n.status == NurseStatus.PREGNANT:
                self.forbid([self.lit(n, d, Duty.N)], self.weight("PREGNANT_NIGHT"))
            if n.duty_role == DutyRole.CHARGE and self.weight("CHARGE_DAY_ONLY") is not SKIP:
                allowed = rules.parameter("CHARGE_DAY_ONLY", "allowedDuties")
                for x in (Duty.D, Duty.E, Duty.N):
                    if x not in allowed:
                        self.forbid([self.lit(n, d, x)], self.weight("CHARGE_DAY_ONLY"))
            if n.duty_role == DutyRole.NIGHT_ONLY:
                for x in (Duty.D, Duty.E):
                    self.forbid([self.lit(n, d, x)], self.weight("NIGHT_ONLY_SHIFT"))

    def _nurse_soft(self, n):
        r, days = self.r, self.days
        one = timedelta(days=1)
        w = self.weight("WISH_OFF_IGNORED")
        for d in r.wish_offs.get(n.id, set()):
            if (n.id, d) in self.free:
                self.forbid([self.lit(n, d, WORK)], w)
        w = self.weight("WISH_DUTY_IGNORED")
        for d, x in r.wish_duties.get(n.id, {}).items():
            if (n.id, d) in self.free:
                self.forbid([_neg(self.lit(n, d, x))], w)
        w = self.weight("MAX_MONTHLY_NIGHTS")
        if w is not SKIP:
            limit = (NIGHT_ONLY_MAX_NIGHTS if n.duty_role == DutyRole.NIGHT_ONLY
                     else self.rules.parameter("MAX_MONTHLY_NIGHTS", "max"))
            self.at_most(self.count(n, days, Duty.N), limit, w, len(days))
        if not n.weekday_only and n.status != NurseStatus.ON_LEAVE:
            # O는 기본 HARD·편차 0. 명시적으로 SOFT로 낮춘 경우에만 감점한다.
            rest = self.count(n, [d for d in days if n.affiliated(d)], Duty.O)
            w = self.weight("OFF_TARGET")
            if w is not SKIP:
                target, dev = off_target(r, n), self.rules.parameter("OFF_TARGET", "maxDeviation")
                self.at_least(rest, target - dev, w)
                self.at_most(rest, target + dev, w, len(days))
                self.at_least(rest, target, OFF_EXACT_WEIGHT)
                self.at_most(rest, target, OFF_EXACT_WEIGHT, len(days))
        w_nod, w_iso, w_blk = self.weight("NIGHT_OFF_DAY"), self.weight("ISOLATED_SHIFT"), self.weight("SHIFT_BLOCK")
        for d in days:
            if self.known(n, d - one) and self.known(n, d - 2 * one):
                self.forbid([self.lit(n, d - 2 * one, Duty.N), self.lit(n, d - one, REST),
                             self.lit(n, d, Duty.D)], w_nod)
            if self.known(n, d - one) and self.known(n, d + one) and self.known(n, d):
                self.forbid([self.lit(n, d - one, REST), self.lit(n, d, WORK), self.lit(n, d + one, REST)], w_iso)
                self.forbid([self.lit(n, d - one, WORK), self.lit(n, d, REST), self.lit(n, d + one, WORK)], w_iso)
                # 하루짜리 시프트: 앞이나 뒤가 다른 근무 (앞뒤 모두 휴무면 고립 배치에서 감점)
                for x in (Duty.D, Duty.E, Duty.N):
                    alone = [self.lit(n, d, x), _neg(self.lit(n, d - one, x)), _neg(self.lit(n, d + one, x))]
                    self.forbid(alone + [self.lit(n, d - one, WORK)], w_blk)
                    self.forbid(alone + [self.lit(n, d + one, WORK)], w_blk)
        self._weekly_rest(n)
        w = self.weight("PRECEPTOR_MISMATCH")
        for c in n.preceptees:
            child = next((o for o in r.nurses if o.id == c), None)
            if child is None:
                continue
            for d in days:
                for x in (Duty.D, Duty.E, Duty.N):
                    self.forbid([self.lit(n, d, x), self.lit(child, d, WORK), _neg(self.lit(child, d, x))], w)

    def _weekly_rest(self, n):
        w = self.weight("WEEKLY_REST")
        if w is SKIP:
            return
        for monday in (d for d in self.days if d.weekday() == 0 and d + timedelta(days=6) <= self.days[-1]):
            week = [monday + timedelta(days=i) for i in range(7)]
            if not all(n.affiliated(d) and self.known(n, d) for d in week):
                continue
            pairs = []
            for a, b in zip(week, week[1:]):
                la, lb = self.lit(n, a, REST), self.lit(n, b, REST)
                if la is True and lb is True:
                    pairs = None  # 이미 고정 칸으로 연속 휴무가 있다
                    break
                if la is False or lb is False:
                    continue
                p = self.m.NewBoolVar("")
                for x in (la, lb):
                    if x is not True:
                        self.m.AddImplication(p, x)
                pairs.append(p)
            if pairs:
                self.at_least(sum(pairs), 1, w)

    def _coverage(self):
        r = self.r
        w = self.weight("COVERAGE", COVERAGE_WEIGHT)
        w_new = self.weight("NEW_NIGHT_ALONE", NEW_ALONE_WEIGHT)
        needs = {"CHARGE_EVERY_SHIFT": has_role(r, {DutyRole.CHARGE}), "SENIOR_EVERY_SHIFT": has_role(r, SENIOR_ROLES)}
        w_skill = self.weight("SKILL_MIX")
        for d in self.days:
            on = [n for n in r.nurses if n.affiliated(d) and n.staff_type == StaffType.NURSE]
            for x in (Duty.D, Duty.E, Duty.N):
                # 최소 인원 부족은 큰 감점으로 찾아낸 뒤 검증기에서 HARD로 보고한다. 초과 E/N은 억제한다.
                assigned = sum(self.lit(n, d, x) * 1 for n in on)
                need = r.rules.required(x)
                self.at_least(assigned, need, w)
                if w is not SKIP:
                    self.at_most(assigned, need, 100 if x in (Duty.E, Duty.N) else 1, len(on))
                if w_skill is not SKIP and need > 0:
                    # 평균 숙련도를 목표에 가깝게: |Σ(숙련도 - 목표)| 감점, 허용 범위(±SKILL_TOLERANCE)를 벗어나면 크게
                    goal = r.rules.parameter("SKILL_MIX", "target")
                    shift = [n for n in on if not n.weekday_only]  # 검증기와 같이 평일 D 고정은 뺀다
                    gap = sum(self.lit(n, d, x) * (n.skill_level - goal) for n in shift)
                    dev = self.m.NewIntVar(0, 5 * len(on), "")
                    self.m.Add(dev >= gap)
                    self.m.Add(dev >= -gap)
                    self.cost.append((w_skill, dev))
                    out = self.m.NewIntVar(0, 50 * len(on), "")
                    self.m.Add(out >= 10 * dev - int(10 * SKILL_TOLERANCE) * sum(self.lit(n, d, x) * 1 for n in shift))
                    self.cost.append((SKILL_OUT_WEIGHT, out))
            for code, roles in (("CHARGE_EVERY_SHIFT", {DutyRole.CHARGE}), ("SENIOR_EVERY_SHIFT", SENIOR_ROLES)):
                if not needs[code]:
                    continue
                for x in (Duty.D, Duty.E, Duty.N):
                    if r.rules.required(x) > 0:
                        self.at_least(sum(self.lit(n, d, x) * 1 for n in on if n.duty_role in roles), 1,
                                      self.weight(code, COVERAGE_WEIGHT))
            if w_new is SKIP:
                continue
            veterans = sum(self.lit(n, d, Duty.N) * 1 for n in on if n.duty_role != DutyRole.NEW)
            k = r.rules.parameter("NEW_NIGHT_ALONE", "minExperienced")
            for n in on:
                if n.duty_role == DutyRole.NEW and self.lit(n, d, Duty.N) is not False:
                    # 신입이 N이면 신입 아닌 N이 k명 이상
                    slack = self.m.NewIntVar(0, k, "")
                    self.m.Add(veterans + slack >= k * self.lit(n, d, Duty.N))
                    self.cost.append((w_new, slack))

    def _fairness(self):
        """그 달 내내 소속이고 근무 가능한 간호사끼리 야간·주말 근무·총 근무 횟수 차이를 줄인다"""
        days = self.days
        full = [n for n in self.r.nurses if n.affiliated(days[0]) and n.affiliated(days[-1])
                and n.status != NurseStatus.ON_LEAVE and not n.weekday_only]
        shift = [n for n in full if n.duty_role != DutyRole.NIGHT_ONLY]
        weekend = [d for d in days if d.weekday() >= 5]
        night_ok = [n for n in shift if n.status != NurseStatus.PREGNANT and n.duty_role != DutyRole.CHARGE]
        # 나이트 전담·차지는 일반 교대 간호사의 횟수 비교에서 제외한다.
        for group, ds, kind in ((night_ok, days, Duty.N), (shift, days, Duty.E), (shift, weekend, WORK), (shift, days, WORK)):
            if len(group) < 2:
                continue
            hi, lo = self.m.NewIntVar(0, len(ds), ""), self.m.NewIntVar(0, len(ds), "")
            for n in group:
                c = self.count(n, ds, kind)
                if kind == WORK and ds == days:
                    c += sum(self.r.cell(n.id, d) == Duty.AL for d in ds)
                self.m.Add(hi >= c)
                self.m.Add(lo <= c)
            self.cost.append((100 if kind in (Duty.E, Duty.N) else FAIRNESS_WEIGHT, hi - lo))

    def hint(self, cells: Cells):
        self.m.ClearHints()
        for (nurse_id, d), vs in self.free.items():
            x = cells.get(nurse_id, {}).get(d, Duty.O)
            for s, v in vs.items():
                self.m.AddHint(v, s == (x if x in SHIFTS else Duty.O))

    def cells(self, solver) -> Cells:
        out: Cells = {n.id: dict(self.r.cells.get(n.id, {})) for n in self.r.nurses}
        for n in self.r.nurses:
            for d in self.days:
                vs = self.free.get((n.id, d))
                if vs is not None:
                    out[n.id][d] = next(s for s, v in vs.items() if solver.Value(v))
                elif d not in out[n.id] and (x := self.const(n, d)) is not None:
                    out[n.id][d] = x
        return out
