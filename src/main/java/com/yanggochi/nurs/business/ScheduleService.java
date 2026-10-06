package com.yanggochi.nurs.business;

import com.yanggochi.nurs.domain.*;
import com.yanggochi.nurs.persistence.*;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.DayOfWeek;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.YearMonth;
import java.time.format.DateTimeParseException;
import java.util.*;
import java.util.stream.Collectors;

/** SCH-01·05·06·08·10·12, RULE-04, GEN-01·06 */
@Service
public class ScheduleService {
    /** SCH-13 무활동 자동 해제 (🔶 권장값) */
    private static final Duration LOCK_TTL = Duration.ofMinutes(30);

    private final ScheduleRepository schedules;
    private final AssignmentRepository assignments;
    private final NurseRepository nurses;
    private final WardRepository wards;
    private final ShiftRequestRepository requests;
    private final RuleService rules;
    private final MemberService members;
    private final AuditService audit;
    private final NotificationService notifications;
    private final UserRepository users;

    public ScheduleService(ScheduleRepository schedules, AssignmentRepository assignments, NurseRepository nurses,
                           WardRepository wards, ShiftRequestRepository requests, RuleService rules,
                           MemberService members, AuditService audit, NotificationService notifications,
                           UserRepository users) {
        this.notifications = notifications;
        this.users = users;
        this.schedules = schedules;
        this.assignments = assignments;
        this.nurses = nurses;
        this.wards = wards;
        this.requests = requests;
        this.rules = rules;
        this.members = members;
        this.audit = audit;
    }

    public record CellEdit(@NotNull Long nurseId, @NotNull LocalDate date, Duty duty) {
    }

    public record Unconfirm(@NotBlank String reason) {
    }

    /** nightBlocked는 임신 여부를 드러내므로 수간호사 응답에만 채운다 (COM-01) */
    public record Row(long nurseId, String name, DutyRole dutyRole, LocalDate affiliationStart,
                      LocalDate affiliationEnd, Boolean nightBlocked, Map<LocalDate, Duty> cells) {
    }

    public record Stat(long nurseId, int d, int e, int n, int off, int al, int offTarget, int weekend, int holiday) {
    }

    /** lock은 수간호사 응답에만, 잠금이 없거나 만료면 null */
    public record ScheduleView(long id, String yearMonth, ScheduleStatus status, int offTarget, Rules rules,
                               List<RuleService.HolidayView> holidays, List<Row> rows,
                               Map<LocalDate, Map<Duty, Integer>> coverage, List<Stat> stats, LockView lock,
                               ConfirmationView confirmation, Readiness readiness) {
    }

    public record LockView(long userId, String name, Instant lastActivity) {
    }

    /** SCH-12 확정·확정 취소 이력. 일반 간호사에게도 보인다 (취소 사유는 알림으로도 공지됨) */
    public record ConfirmationView(Instant confirmedAt, String confirmedBy, Instant unconfirmedAt, String unconfirmedBy,
                                   String unconfirmReason, int confirmCount) {
    }

    /** 수간호사 전용: 확정 버튼 활성 여부 판단용. confirmable = 초안 + 하드 위반 0 + 대기 신청 0 */
    public record Readiness(int hardViolations, int softViolations, int pendingRequests, boolean confirmable) {
    }

    public record Confirm(boolean acknowledgeSoft) {
    }

    /** GEN-05 고정 셀 */
    public record FixedCell(@NotNull Long nurseId, @NotNull LocalDate date) {
    }

    public record GenerateResult(boolean solved, double wishOffRate, List<Violation> violations) {
    }

    @Transactional
    public ScheduleView create(long userId, String ym) {
        Member m = members.requireHead(userId);
        YearMonth month = parse(ym);
        schedules.findByWardIdAndYearMonth(m.wardId(), month.toString())
                .ifPresent(s -> { throw ApiException.conflict("이미 해당 월 근무표가 있습니다", s.id); });
        Schedule s = new Schedule();
        s.wardId = m.wardId();
        s.yearMonth = month.toString();
        schedules.save(s);
        Set<Long> rowIds = roster(s).nurses().stream().map(NurseInfo::id).collect(Collectors.toSet());
        approved(m.wardId(), month, RequestType.ANNUAL_LEAVE).stream()
                .filter(r -> rowIds.contains(r.nurseId))
                .forEach(r -> assignments.save(new Assignment(s.id, r.nurseId, r.date, Duty.AL)));
        audit.log(m.wardId(), userId, "SCHEDULE_CREATED", "schedule:" + s.yearMonth, null, null);
        return view(s, m);
    }

    /** REQ-05·SCH-08. 일반 간호사에게 DRAFT·GENERATING은 존재하지 않는 것처럼 404 */
    public ScheduleView get(long userId, String ym) {
        Member m = members.require(userId);
        Schedule s = find(m, ym);
        if (!m.isHead() && !(s.status == ScheduleStatus.CONFIRMED || s.status == ScheduleStatus.ARCHIVED))
            throw ApiException.notFound();
        return view(s, m);
    }

    /** SCH-02~04. 범위·일괄 편집도 한 요청으로 처리한다 */
    @Transactional
    public List<Violation> editCells(long userId, String ym, List<CellEdit> edits) {
        Member m = members.requireHead(userId);
        Schedule s = draft(m, ym);
        hold(s, m);
        Roster r = roster(s);
        Map<Long, NurseInfo> byId = r.nurses().stream().collect(Collectors.toMap(NurseInfo::id, n -> n));
        Map<String, Assignment> existing = new HashMap<>();
        assignments.findByScheduleId(s.id).forEach(a -> existing.put(a.nurseId + "/" + a.date, a));
        for (CellEdit e : edits) {
            NurseInfo n = byId.get(e.nurseId());
            if (n == null) throw ApiException.notFound();
            if (!YearMonth.from(e.date()).equals(r.month())) throw ApiException.badRequest("해당 월 날짜가 아닙니다: " + e.date());
            if (!n.affiliated(e.date())) throw ApiException.badRequest("소속 기간 밖 셀은 편집할 수 없습니다: " + e.date());
            if (e.duty() == Duty.AL) throw ApiException.badRequest("AL은 연차 승인으로만 입력됩니다");
            Assignment a = existing.get(e.nurseId() + "/" + e.date());
            if (e.duty() == null) {
                if (a != null) assignments.delete(a);
            } else if (a != null) {
                a.duty = e.duty();
            } else {
                existing.put(e.nurseId() + "/" + e.date(), assignments.save(new Assignment(s.id, e.nurseId(), e.date(), e.duty())));
            }
        }
        assignments.flush();
        return ScheduleValidator.validate(roster(s));
    }

    public List<Violation> violations(long userId, String ym) {
        Member m = members.requireHead(userId);
        return ScheduleValidator.validate(roster(find(m, ym)));
    }

    /** SCH-12. 하드 위반 0건이어야 확정. 소프트 위반은 응답으로 돌려준다 */
    @Transactional
    public List<Violation> confirm(long userId, String ym, boolean acknowledgeSoft) {
        Member m = members.requireHead(userId);
        Schedule s = draft(m, ym);
        // 리뷰 #2: 확정 후에는 승인해도 반영할 곳이 없으므로, 대기 중인 신청부터 처리하게 한다
        List<Long> pending = pendingRequests(s);
        if (!pending.isEmpty()) throw ApiException.conflict("처리하지 않은 신청 " + pending.size() + "건이 있습니다", pending);
        List<Violation> v = ScheduleValidator.validate(roster(s));
        if (ScheduleValidator.hasHard(v))
            throw ApiException.conflict("하드 위반이 있어 확정할 수 없습니다", v.stream().filter(x -> x.severity() == Severity.HARD).toList());
        // SCH-12: 소프트 위반은 "확인 후" 확정 가능. 확인 없이 보내면 목록을 돌려준다
        if (!v.isEmpty() && !acknowledgeSoft)
            throw ApiException.conflict("소프트 위반 " + v.size() + "건을 확인한 뒤 확정하세요", v);
        s.status = ScheduleStatus.CONFIRMED;
        s.lockedBy = null;
        s.confirmedAt = Instant.now();
        s.confirmedBy = userId;
        s.confirmCount++;
        schedules.findByWardIdAndStatusIn(m.wardId(), List.of(ScheduleStatus.CONFIRMED)).stream()
                .filter(o -> o.yearMonth.compareTo(s.yearMonth) < 0)
                .forEach(o -> o.status = ScheduleStatus.ARCHIVED);
        audit.log(m.wardId(), userId, "SCHEDULE_CONFIRMED", "schedule:" + s.yearMonth, ScheduleStatus.DRAFT, ScheduleStatus.CONFIRMED);
        notifications.notifyWard(m.wardId(), NotificationType.SCHEDULE_CONFIRMED, s.confirmCount > 1
                ? s.yearMonth + " 근무표가 다시 확정되었습니다. 변경된 근무를 확인하세요"
                : s.yearMonth + " 근무표가 확정되었습니다");
        return v;
    }

    @Transactional
    public void unconfirm(long userId, String ym, String reason) {
        Member m = members.requireHead(userId);
        Schedule s = find(m, ym);
        if (s.status == ScheduleStatus.ARCHIVED) throw ApiException.conflict("다음 달이 확정되어 보관된 근무표는 취소할 수 없습니다");
        if (s.status != ScheduleStatus.CONFIRMED) throw ApiException.conflict("확정 상태가 아닙니다");
        s.status = ScheduleStatus.DRAFT;
        s.unconfirmedAt = Instant.now();
        s.unconfirmedBy = userId;
        s.unconfirmReason = reason;
        audit.log(m.wardId(), userId, "SCHEDULE_UNCONFIRMED", "schedule:" + s.yearMonth, "reason=" + reason, ScheduleStatus.DRAFT);
        notifications.notifyWard(m.wardId(), NotificationType.SCHEDULE_UNCONFIRMED,
                s.yearMonth + " 근무표 확정이 취소되었습니다. 재확정 시 다시 알려드립니다. 사유: " + reason);
    }

    /**
     * GEN-01. 동기 실행이라 GENERATING 상태는 트랜잭션 밖에서 관찰되지 않는다.
     * ponytail: 솔버가 수 초를 넘기면 비동기 + GENERATING 잠금 + 진행률(GEN-02)로 전환
     */
    @Transactional
    public GenerateResult generate(long userId, String ym, List<FixedCell> fixed) {
        Member m = members.requireHead(userId);
        Schedule s = draft(m, ym);
        hold(s, m);
        Roster r = roster(s);
        if (r.nurses().isEmpty()) throw ApiException.badRequest("소속 간호사가 없습니다");
        // 연차(AL)와 GEN-05 고정 셀만 남기고 나머지를 다시 짠다. 고정 셀이 하드 위반이면 GEN-06(solved=false)
        Set<String> keep = new HashSet<>();
        if (fixed != null) fixed.forEach(f -> keep.add(f.nurseId() + "/" + f.date()));
        Map<Long, Map<LocalDate, Duty>> kept = new HashMap<>();
        r.cells().forEach((nurseId, row) -> row.forEach((d, x) -> {
            if (x == Duty.AL || keep.contains(nurseId + "/" + d)) kept.computeIfAbsent(nurseId, k -> new HashMap<>()).put(d, x);
        }));
        Map<Long, Map<LocalDate, Duty>> cells = ScheduleGenerator.generate(r.withCells(kept));
        assignments.deleteByScheduleId(s.id);
        assignments.flush();
        cells.forEach((nurseId, row) -> row.forEach((d, duty) -> assignments.save(new Assignment(s.id, nurseId, d, duty))));
        assignments.flush();

        Roster after = roster(s);
        List<Violation> v = ScheduleValidator.validate(after);
        long wishes = r.wishOffs().values().stream().mapToLong(Set::size).sum();
        long granted = r.wishOffs().entrySet().stream()
                .flatMap(e -> e.getValue().stream().filter(d -> {
                    Duty x = after.cell(e.getKey(), d);
                    return x == Duty.O || x == Duty.AL;
                })).count();
        audit.log(m.wardId(), userId, "SCHEDULE_GENERATED", "schedule:" + s.yearMonth, null, "hard=" + v.stream().filter(x -> x.severity() == Severity.HARD).count());
        // GEN-06: 해가 없으면 solved=false + 남은 하드 위반(충돌 원인)과 부분 해를 그대로 돌려준다
        return new GenerateResult(!ScheduleValidator.hasHard(v), wishes == 0 ? 1.0 : (double) granted / wishes, v);
    }

    /** RULE-04 수동 조정. null이면 자동 계산으로 복귀 */
    @Transactional
    public int setOffTarget(long userId, String ym, Integer target) {
        Member m = members.requireHead(userId);
        Schedule s = find(m, ym);
        if (target != null && target < 0) throw ApiException.badRequest("0 이상이어야 합니다");
        Integer before = s.offTarget;
        s.offTarget = target;
        audit.log(m.wardId(), userId, "RULES_CHANGED", "offTarget:" + s.yearMonth, before, target);
        return offTarget(s);
    }

    /** SCH-13. 먼저 잡은 수간호사가 편집권. force면 강제로 가져오고 기존 보유자에게 알린다 */
    @Transactional
    public LockView lock(long userId, String ym, boolean force) {
        Member m = members.requireHead(userId);
        Schedule s = draft(m, ym);
        if (lockedByOther(s, userId)) {
            if (!force) throw ApiException.conflict(lockView(s).name() + "님이 편집 중입니다", lockView(s));
            notifications.notifyUser(s.lockedBy, NotificationType.LOCK_FORCED,
                    s.yearMonth + " 근무표 편집권을 " + users.findById(userId).map(u -> u.name).orElse("") + "님이 가져갔습니다");
            audit.log(m.wardId(), userId, "LOCK_FORCED", "schedule:" + s.yearMonth, "user:" + s.lockedBy, "user:" + userId);
        }
        s.lockedBy = userId;
        s.lockedAt = Instant.now();
        return lockView(s);
    }

    @Transactional
    public void unlock(long userId, String ym) {
        Member m = members.requireHead(userId);
        Schedule s = find(m, ym);
        if (s.lockedBy != null && s.lockedBy == userId) s.lockedBy = null;
    }

    /** 편집 작업 공통: 남이 잡고 있으면 409, 아니면 잠금 획득·갱신 */
    private void hold(Schedule s, Member m) {
        if (lockedByOther(s, m.userId())) throw ApiException.conflict(lockView(s).name() + "님이 편집 중입니다", lockView(s));
        s.lockedBy = m.userId();
        s.lockedAt = Instant.now();
    }

    private boolean lockedByOther(Schedule s, long userId) {
        return s.lockedBy != null && s.lockedBy != userId && s.lockedAt.plus(LOCK_TTL).isAfter(Instant.now());
    }

    private LockView lockView(Schedule s) {
        if (s.lockedBy == null || s.lockedAt.plus(LOCK_TTL).isBefore(Instant.now())) return null;
        return new LockView(s.lockedBy, users.findById(s.lockedBy).map(u -> u.name).orElse(null), s.lockedAt);
    }

    private List<Long> pendingRequests(Schedule s) {
        YearMonth month = YearMonth.parse(s.yearMonth);
        return requests.findByWardIdAndStatusAndDateBetween(s.wardId, RequestStatus.PENDING, month.atDay(1), month.atEndOfMonth())
                .stream().map(q -> q.id).toList();
    }

    private ConfirmationView confirmation(Schedule s) {
        if (s.confirmCount == 0 && s.unconfirmedAt == null) return null;
        return new ConfirmationView(s.confirmedAt, userName(s.confirmedBy), s.unconfirmedAt, userName(s.unconfirmedBy),
                s.unconfirmReason, s.confirmCount);
    }

    private Readiness readiness(Schedule s, Roster r) {
        List<Violation> v = ScheduleValidator.validate(r);
        int hard = (int) v.stream().filter(x -> x.severity() == Severity.HARD).count();
        int pending = pendingRequests(s).size();
        return new Readiness(hard, v.size() - hard, pending, s.status == ScheduleStatus.DRAFT && hard == 0 && pending == 0);
    }

    private String userName(Long id) {
        return id == null ? null : users.findById(id).map(u -> u.name).orElse(null);
    }

    /** NUR-04·05·09에서 호출. 확정본은 자동 변경하지 않고 위반만 돌려준다 */
    List<Violation> violationsFor(long wardId, long nurseId) {
        return schedules.findByWardIdAndStatusIn(wardId, List.of(ScheduleStatus.DRAFT, ScheduleStatus.CONFIRMED)).stream()
                .flatMap(s -> ScheduleValidator.validate(roster(s)).stream())
                .filter(v -> v.nurseId() != null && v.nurseId() == nurseId)
                .toList();
    }

    /** 연차 승인·취소 시 DRAFT 근무표에 AL 반영 */
    void syncLeave(long wardId, long nurseId, LocalDate date, boolean add) {
        schedules.findByWardIdAndYearMonth(wardId, YearMonth.from(date).toString())
                .filter(s -> s.status == ScheduleStatus.DRAFT)
                .ifPresent(s -> {
                    Optional<Assignment> a = assignments.findByScheduleId(s.id).stream()
                            .filter(x -> x.nurseId == nurseId && x.date.equals(date)).findFirst();
                    if (add) a.ifPresentOrElse(x -> x.duty = Duty.AL, () -> assignments.save(new Assignment(s.id, nurseId, date, Duty.AL)));
                    else a.filter(x -> x.duty == Duty.AL).ifPresent(assignments::delete);
                });
    }

    boolean isLocked(long wardId, LocalDate date) {
        return schedules.findByWardIdAndYearMonth(wardId, YearMonth.from(date).toString())
                .map(s -> s.status == ScheduleStatus.CONFIRMED || s.status == ScheduleStatus.ARCHIVED).orElse(false);
    }

    private ScheduleView view(Schedule s, Member m) {
        Roster r = roster(s);
        Set<LocalDate> holidays = rules.inMonth(s.wardId, r.month()).stream().map(h -> h.date).collect(Collectors.toSet());
        List<Row> rows = r.nurses().stream()
                .map(n -> new Row(n.id(), n.name(), n.dutyRole(), n.affiliationStart(), n.affiliationEnd(),
                        m.isHead() ? n.status() == NurseStatus.PREGNANT : null,
                        new TreeMap<>(r.cells().getOrDefault(n.id(), Map.of()))))
                .toList();
        Map<LocalDate, Map<Duty, Integer>> coverage = new TreeMap<>();
        r.days().forEach(d -> coverage.put(d, ScheduleValidator.coverage(r, d)));
        List<Stat> stats = r.nurses().stream()
                .filter(n -> m.isHead() || n.id() == m.nurseId())
                .map(n -> stat(r, n.id(), holidays))
                .toList();
        return new ScheduleView(s.id, s.yearMonth, s.status, r.offTarget(), r.rules(),
                rules.inMonth(s.wardId, r.month()).stream().map(h -> new RuleService.HolidayView(h.id, h.date, h.name)).toList(),
                rows, coverage, stats, m.isHead() ? lockView(s) : null, confirmation(s), m.isHead() ? readiness(s, r) : null);
    }

    private Stat stat(Roster r, long nurseId, Set<LocalDate> holidays) {
        Map<Duty, Integer> c = new EnumMap<>(Duty.class);
        int weekend = 0, holiday = 0;
        for (LocalDate d : r.days()) {
            Duty x = r.cell(nurseId, d);
            if (x == null) continue;
            c.merge(x, 1, Integer::sum);
            if (x.isWork() && (d.getDayOfWeek() == DayOfWeek.SATURDAY || d.getDayOfWeek() == DayOfWeek.SUNDAY)) weekend++;
            if (x.isWork() && holidays.contains(d)) holiday++;
        }
        return new Stat(nurseId, c.getOrDefault(Duty.D, 0), c.getOrDefault(Duty.E, 0), c.getOrDefault(Duty.N, 0),
                c.getOrDefault(Duty.O, 0), c.getOrDefault(Duty.AL, 0), r.offTarget(), weekend, holiday);
    }

    /** SCH-08: 당시 소속 인원 기준. 퇴사자도 소속 기간이 겹치면 행으로 남는다 */
    private Roster roster(Schedule s) {
        YearMonth month = YearMonth.parse(s.yearMonth);
        Ward w = wards.findById(s.wardId).orElseThrow(ApiException::notFound);
        List<NurseInfo> ns = nurses.findByWardId(s.wardId).stream()
                .filter(n -> !n.affiliationStart.isAfter(month.atEndOfMonth())
                        && (n.affiliationEnd == null || !n.affiliationEnd.isBefore(month.atDay(1))))
                .sorted(Comparator.comparing((Nurse n) -> n.name).thenComparing(n -> n.id))
                .map(Nurse::info)
                .toList();
        Map<Long, Map<LocalDate, Duty>> cells = new HashMap<>();
        assignments.findByScheduleId(s.id).forEach(a -> cells.computeIfAbsent(a.nurseId, k -> new HashMap<>()).put(a.date, a.duty));
        Map<Long, Set<LocalDate>> wish = new HashMap<>();
        approved(s.wardId, month, RequestType.WISH_OFF)
                .forEach(q -> wish.computeIfAbsent(q.nurseId, k -> new HashSet<>()).add(q.date));
        Map<Long, Map<LocalDate, Duty>> wishDuties = new HashMap<>();
        approved(s.wardId, month, RequestType.WISH_DUTY)
                .forEach(q -> wishDuties.computeIfAbsent(q.nurseId, k -> new HashMap<>()).put(q.date, q.duty));
        // 리뷰 #1: 연속 야간·근무를 월 경계 너머로 세기 위해 이전 달 근무표를 함께 넘긴다
        Map<Long, Map<LocalDate, Duty>> before = new HashMap<>();
        schedules.findByWardIdAndYearMonth(s.wardId, month.minusMonths(1).toString()).ifPresent(prev ->
                assignments.findByScheduleId(prev.id).forEach(a -> before.computeIfAbsent(a.nurseId, k -> new HashMap<>()).put(a.date, a.duty)));
        return new Roster(month, ns, cells, w.rules(), offTarget(s), wish, wishDuties, before);
    }

    /** RULE-04: 공휴일 수 + 1. ponytail: 주휴일 포함 여부 🔶 미확정이라 공휴일만 센다 */
    private int offTarget(Schedule s) {
        return s.offTarget != null ? s.offTarget : rules.inMonth(s.wardId, YearMonth.parse(s.yearMonth)).size() + 1;
    }

    private List<ShiftRequest> approved(long wardId, YearMonth month, RequestType type) {
        return requests.findByWardIdAndStatusAndDateBetween(wardId, RequestStatus.APPROVED, month.atDay(1), month.atEndOfMonth())
                .stream().filter(r -> r.type == type).toList();
    }

    private Schedule find(Member m, String ym) {
        return schedules.findByWardIdAndYearMonth(m.wardId(), parse(ym).toString()).orElseThrow(ApiException::notFound);
    }

    private Schedule draft(Member m, String ym) {
        Schedule s = find(m, ym);
        if (s.status != ScheduleStatus.DRAFT) throw ApiException.conflict("초안(DRAFT) 상태에서만 가능합니다. 현재: " + s.status);
        return s;
    }

    private static YearMonth parse(String ym) {
        try {
            return YearMonth.parse(ym);
        } catch (DateTimeParseException e) {
            throw ApiException.badRequest("연월 형식은 yyyy-MM 입니다");
        }
    }
}
