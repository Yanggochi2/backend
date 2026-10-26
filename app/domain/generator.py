"""
GEN-01. OR-Tools CP-SAT으로 근무표를 편성한다. 규칙 기준: docs/superpowers/specs/2026-10-26-cp-sat-generator-design.md
- 입력 셀(연차 AL, GEN-05 고정 셀)은 그대로 두고 빈 칸만 채운다. 휴직자는 O, 소속 기간 밖은 칸이 없다
- HARD로 켜진 규칙은 제약으로, SOFT 규칙과 SOFT로 낮춘 하드 규칙은 감점으로 최적화한다
- 인원 충족(COVERAGE)·신입 단독 야간 금지는 아주 큰 감점으로 둔다. 인원이 부족한 달에도 결과를 내고,
  남은 하드 위반은 검증기가 잡아 해 없음(GEN-04) 흐름으로 간다
- 고정 칸끼리만으로 이미 어긋나는 구간(지난달 말과 이어지는 구간 등)은 제약에서 뺀다. 그 위반은 검증기가 보고한다
"""
import os
from datetime import date, timedelta

from ortools.sat.python import cp_model

from app.domain import greedy
from app.domain.model import NIGHT_ONLY_MAX_NIGHTS, SENIOR_ROLES, Cells, Duty, DutyRole, NurseStatus, Roster, Severity
from app.domain.validator import has_role

SHIFTS = (Duty.D, Duty.E, Duty.N, Duty.O)
COVERAGE_WEIGHT = 10_000
NEW_ALONE_WEIGHT = 20_000  # 신입 혼자 야간보다 인원 부족으로 남기는 편이 낫다
SOFTENED_WEIGHT = 500      # SOFT로 낮춘 하드 규칙
WEIGHTS = {"WISH_OFF_IGNORED": 40, "MAX_MONTHLY_NIGHTS": 50, "OFF_TARGET": 30, "NIGHT_OFF_DAY": 20,
           "WISH_DUTY_IGNORED": 20, "WEEKLY_REST": 15, "ISOLATED_SHIFT": 10, "SHIFT_BLOCK": 5,
           "PRECEPTOR_MISMATCH": 5}
FAIRNESS_WEIGHT = 10
REVERSE = ((Duty.E, Duty.D), (Duty.N, Duty.D), (Duty.N, Duty.E))  # 근무 사이 휴식이 11시간 미만
LOOKBACK_DAYS = 7  # 지난달 말 근무를 이어서 볼 일수 (연속 근무 최대 5일 + 여유)
WORK, REST = "WORK", "REST"
SKIP = object()  # 꺼진 규칙
# 탐색 시간 기본값(초). 테스트는 짧게 둔다
DEFAULT_SECONDS = float(os.getenv("GENERATION_DEFAULT_SECONDS", "10"))


def generate(r: Roster, max_seconds: float | None = None) -> Cells:
    model = _Model(r)
    model.build()
    # 그리디 결과를 시작점으로 준다. 짧은 시간 제한에서도 첫 해를 바로 찾고, 실패 시 대체 결과로도 쓴다
    fallback = greedy.generate(r)
    model.hint(fallback)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_seconds or DEFAULT_SECONDS
    solver.parameters.num_workers = 8
    if solver.Solve(model.m) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return fallback
    return model.cells(solver)


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
            return vs[Duty.O].Not() if kind == WORK else vs[Duty.O] if kind == REST else vs[kind]
        x = self.const(n, d)
        work = x is not None and x.is_work
        return work if kind == WORK else not work if kind == REST else x == kind

    def count(self, n, days, kind):
        """days 중 kind인 칸 수 (선형식)"""
        return sum(self.lit(n, d, kind) * 1 for d in days)

    # --- 제약 도구 ---
    def weight(self, code: str, hard_weight=None):
        """꺼짐이면 SKIP, HARD면 hard_weight(None이면 제약), SOFT로 낮춘 하드 규칙은 SOFTENED_WEIGHT"""
        mode = self.rules.mode(code)
        if mode is None:
            return SKIP
        if code in WEIGHTS:
            return WEIGHTS[code]
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
        short = self.m.NewIntVar(0, need, "")
        self.m.Add(expr + short >= need)
        self.cost.append((weight, short))

    def at_most(self, expr, limit: int, weight, upper: int):
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
        self.m.Minimize(sum(w * e for w, e in self.cost))

    def _nurse_hard(self, n):
        rules, t = self.rules, self.timeline
        if rules.forbid_night_to_day:
            w = self.weight("NIGHT_TO_DAY")
            for a, b in zip(t, t[1:]):
                for x, y in REVERSE:
                    self.forbid([self.lit(n, a, x), self.lit(n, b, y)], w)
        w = self.weight("MAX_CONSECUTIVE_NIGHTS")
        k = rules.max_consecutive_nights
        for i in range(len(t) - k):
            self.forbid([self.lit(n, d, Duty.N) for d in t[i:i + k + 1]], w)
        w = self.weight("MAX_CONSECUTIVE_WORK")
        k = rules.max_consecutive_work_days
        for i in range(len(t) - k):
            self.forbid([self.lit(n, d, WORK) for d in t[i:i + k + 1]], w)
        w = self.weight("NIGHT_REST")
        for i in range(1, len(t) - 1):
            nn = [self.lit(n, t[i - 1], Duty.N), self.lit(n, t[i], Duty.N)]
            for x in (Duty.D, Duty.E):
                self.forbid(nn + [self.lit(n, t[i + 1], x)], w)
            if i + 2 < len(t):
                self.forbid(nn + [_neg(self.lit(n, t[i + 1], Duty.N)), self.lit(n, t[i + 2], WORK)], w)
        for d in self.days:
            if n.status == NurseStatus.PREGNANT:
                self.forbid([self.lit(n, d, Duty.N)], self.weight("PREGNANT_NIGHT"))
            if n.duty_role == DutyRole.CHARGE:
                for x in (Duty.E, Duty.N):
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
            limit = NIGHT_ONLY_MAX_NIGHTS if n.duty_role == DutyRole.NIGHT_ONLY else self.rules.max_monthly_nights
            self.at_most(self.count(n, days, Duty.N), limit, w, len(days))
        if n.affiliated(days[0]) and n.affiliated(days[-1]):
            self.at_least(self.count(n, days, REST), r.off_target, self.weight("OFF_TARGET"))
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
        for d in self.days:
            on = [n for n in r.nurses if n.affiliated(d)]
            for x in (Duty.D, Duty.E, Duty.N):
                assigned = sum(self.lit(n, d, x) * 1 for n in on)
                self.at_least(assigned, r.rules.required(x), w)
                # 필요 인원보다 많이 넣지 않는다 (고정 칸이 이미 더 많으면 그 수까지)
                fixed = sum(1 for n in on if (n.id, d) not in self.free and self.const(n, d) == x)
                self.m.Add(assigned <= max(r.rules.required(x), fixed))
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
            for n in on:
                if n.duty_role == DutyRole.NEW and self.lit(n, d, Duty.N) is not False:
                    slack = self.m.NewBoolVar("")
                    self.m.Add(self.lit(n, d, Duty.N) * 1 <= veterans + slack)
                    self.cost.append((w_new, slack))

    def _fairness(self):
        """그 달 내내 소속이고 근무 가능한 간호사끼리 야간·주말 근무·총 근무 횟수 차이를 줄인다"""
        days = self.days
        full = [n for n in self.r.nurses if n.affiliated(days[0]) and n.affiliated(days[-1])
                and n.status != NurseStatus.ON_LEAVE]
        shift = [n for n in full if n.duty_role != DutyRole.NIGHT_ONLY]
        weekend = [d for d in days if d.weekday() >= 5]
        night_ok = [n for n in shift if n.status != NurseStatus.PREGNANT and n.duty_role != DutyRole.CHARGE]
        # 나이트 전담은 야간·주말 비교에서 빼고 총 근무일수만 맞춘다 → 근무일이 모두 나이트가 되어 나이트를 맡는다
        for group, ds, kind in ((night_ok, days, Duty.N), (shift, weekend, WORK), (full, days, WORK)):
            if len(group) < 2:
                continue
            hi, lo = self.m.NewIntVar(0, len(ds), ""), self.m.NewIntVar(0, len(ds), "")
            for n in group:
                c = self.count(n, ds, kind)
                self.m.Add(hi >= c)
                self.m.Add(lo <= c)
            self.cost.append((FAIRNESS_WEIGHT, hi - lo))

    def hint(self, cells: Cells):
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
