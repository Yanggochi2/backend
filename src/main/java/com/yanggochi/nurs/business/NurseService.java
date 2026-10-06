package com.yanggochi.nurs.business;

import com.yanggochi.nurs.domain.*;
import com.yanggochi.nurs.persistence.Nurse;
import com.yanggochi.nurs.persistence.NurseRepository;
import jakarta.validation.constraints.*;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.util.Comparator;
import java.util.List;
import java.util.Set;

/** NUR-01·03·04·05·08·09 */
@Service
public class NurseService {
    private final NurseRepository nurses;
    private final MemberService members;
    private final ScheduleService schedules;
    private final AuditService audit;

    public NurseService(NurseRepository nurses, MemberService members, ScheduleService schedules, AuditService audit) {
        this.nurses = nurses;
        this.members = members;
        this.schedules = schedules;
        this.audit = audit;
    }

    /**
     * COM-02 등록·수정 공통 필드셋. role은 AUTH-07(권한 이관)으로만 바뀌므로 받지 않는다.
     * RETIRED는 NUR-09 퇴사 처리로만 설정한다.
     */
    public record NurseForm(@NotBlank String name, @NotNull DutyRole dutyRole, @NotNull NurseStatus status,
                            @NotNull LocalDate joinedAt, @Min(0) int careerMonths, @Min(1) @Max(5) int skillLevel,
                            @NotNull LocalDate affiliationStart, LocalDate affiliationEnd, Set<Long> preceptorOf) {
    }

    /** 일반 간호사 조회 시 status·joinedAt·careerMonths·skillLevel·preceptorOf는 null (NUR-03) */
    public record NurseView(long id, String name, Role role, DutyRole dutyRole, LocalDate affiliationStart,
                            LocalDate affiliationEnd, NurseStatus status, LocalDate joinedAt, Integer careerMonths,
                            Integer skillLevel, Set<Long> preceptorOf, Boolean hasAccount) {
    }

    public record NurseResult(NurseView nurse, List<Violation> violations, List<String> warnings) {
    }

    public record Retire(@NotNull LocalDate affiliationEnd) {
    }

    public PageResponse<NurseView> list(long userId, boolean includeRetired, Role role, DutyRole dutyRole,
                                        NurseStatus status, String sort, int page, int size) {
        Member m = members.require(userId);
        // 경력순 정렬은 경력을 노출하므로 수간호사만
        Comparator<Nurse> order = m.isHead() && "career".equals(sort)
                ? Comparator.comparingInt((Nurse n) -> n.careerMonths).reversed()
                : Comparator.comparing((Nurse n) -> n.name);
        // ponytail: 병동 단위(수십 명)라 메모리 필터·페이징. 수천 명 규모면 Specification 쿼리로
        List<NurseView> all = nurses.findByWardId(m.wardId()).stream()
                .filter(n -> includeRetired || n.status != NurseStatus.RETIRED)
                .filter(n -> role == null || n.role == role)
                .filter(n -> dutyRole == null || n.dutyRole == dutyRole)
                .filter(n -> status == null || (m.isHead() && n.status == status))
                .sorted(order)
                .map(n -> view(n, m.isHead()))
                .toList();
        int from = Math.min(page * size, all.size());
        return new PageResponse<>(all.subList(from, Math.min(from + size, all.size())), page, size, all.size());
    }

    @Transactional
    public NurseResult register(long userId, NurseForm f) {
        Member m = members.requireHead(userId);
        Nurse n = new Nurse();
        n.wardId = m.wardId();
        apply(n, f, m);
        nurses.save(n);
        audit.log(m.wardId(), userId, "NURSE_REGISTERED", "nurse:" + n.id, null, f);
        return result(m, n);
    }

    @Transactional
    public NurseResult update(long userId, long id, NurseForm f) {
        Member m = members.requireHead(userId);
        Nurse n = nurses.findByIdAndWardId(id, m.wardId()).orElseThrow(ApiException::notFound);
        if (n.status == NurseStatus.RETIRED) throw ApiException.conflict("퇴사자는 수정할 수 없습니다");
        String before = view(n, true).toString();
        apply(n, f, m);
        nurses.flush();
        audit.log(m.wardId(), userId, "NURSE_UPDATED", "nurse:" + n.id, before, view(n, true));
        return result(m, n);
    }

    /** NUR-09. soft delete */
    @Transactional
    public NurseResult retire(long userId, long id, LocalDate end) {
        Member m = members.requireHead(userId);
        Nurse n = nurses.findByIdAndWardId(id, m.wardId()).orElseThrow(ApiException::notFound);
        if (n.status == NurseStatus.RETIRED) throw ApiException.conflict("이미 퇴사 처리되었습니다");
        if (n.role == Role.HEAD_NURSE && nurses.countByWardIdAndRoleAndStatusNot(m.wardId(), Role.HEAD_NURSE, NurseStatus.RETIRED) <= 1)
            throw ApiException.conflict("마지막 수간호사는 권한 이관 후 퇴사할 수 있습니다");
        if (end.isBefore(n.affiliationStart)) throw ApiException.badRequest("소속 종료일이 시작일보다 빠릅니다");
        n.status = NurseStatus.RETIRED;
        n.affiliationEnd = end;
        nurses.flush();
        audit.log(m.wardId(), userId, "NURSE_RETIRED", "nurse:" + n.id, null, end);
        return result(m, n);
    }

    private void apply(Nurse n, NurseForm f, Member m) {
        if (f.status() == NurseStatus.RETIRED) throw ApiException.badRequest("퇴사는 퇴사 처리 기능을 사용하세요");
        if (f.affiliationEnd() != null && f.affiliationEnd().isBefore(f.affiliationStart()))
            throw ApiException.badRequest("소속 종료일이 시작일보다 빠릅니다");
        Set<Long> preceptees = f.preceptorOf() == null ? Set.of() : f.preceptorOf();
        for (Long p : preceptees)
            if (nurses.findByIdAndWardId(p, m.wardId()).isEmpty() || p.equals(n.id)) throw ApiException.notFound();
        n.name = f.name();
        n.dutyRole = f.dutyRole();
        n.status = f.status();
        n.joinedAt = f.joinedAt();
        n.careerMonths = f.careerMonths();
        n.skillLevel = f.skillLevel();
        n.affiliationStart = f.affiliationStart();
        n.affiliationEnd = f.affiliationEnd();
        n.preceptorOf.clear();
        n.preceptorOf.addAll(preceptees);
    }

    private NurseResult result(Member m, Nurse n) {
        boolean noCharge = nurses.findByWardId(m.wardId()).stream()
                .noneMatch(x -> x.dutyRole == DutyRole.CHARGE && x.status != NurseStatus.RETIRED);
        return new NurseResult(view(n, true), schedules.violationsFor(m.wardId(), n.id),
                noCharge ? List.of("병동에 차지 간호사가 없습니다") : List.of());
    }

    private static NurseView view(Nurse n, boolean head) {
        return head
                ? new NurseView(n.id, n.name, n.role, n.dutyRole, n.affiliationStart, n.affiliationEnd, n.status,
                n.joinedAt, n.careerMonths, n.skillLevel, Set.copyOf(n.preceptorOf), n.userId != null)
                : new NurseView(n.id, n.name, n.role, n.dutyRole, n.affiliationStart, n.affiliationEnd,
                null, null, null, null, null, null);
    }
}
