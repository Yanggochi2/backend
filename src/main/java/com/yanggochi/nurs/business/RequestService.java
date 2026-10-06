package com.yanggochi.nurs.business;

import com.yanggochi.nurs.domain.Duty;
import com.yanggochi.nurs.domain.RequestStatus;
import com.yanggochi.nurs.domain.RequestType;
import com.yanggochi.nurs.persistence.NurseRepository;
import com.yanggochi.nurs.persistence.ShiftRequest;
import com.yanggochi.nurs.persistence.ShiftRequestRepository;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.Set;

/** REQ-01·02·03·07 */
@Service
public class RequestService {
    /** REQ-02 🔶 사유 코드 미확정 — 명세 예시값 사용 */
    private static final Set<String> REASON_CODES = Set.of("PERSONAL", "FAMILY", "HEALTH", "STUDY", "ETC");

    private final ShiftRequestRepository requests;
    private final NurseRepository nurses;
    private final MemberService members;
    private final ScheduleService schedules;
    private final AuditService audit;
    private final NotificationService notifications;

    public RequestService(ShiftRequestRepository requests, NurseRepository nurses, MemberService members,
                          ScheduleService schedules, AuditService audit, NotificationService notifications) {
        this.notifications = notifications;
        this.requests = requests;
        this.nurses = nurses;
        this.members = members;
        this.schedules = schedules;
        this.audit = audit;
    }

    public record RequestForm(@NotNull RequestType type, @NotNull LocalDate date, Duty duty, String reasonCode) {
    }

    public record Reject(@NotBlank String reason) {
    }

    public record RequestView(long id, long nurseId, String nurseName, RequestType type, LocalDate date, Duty duty,
                              String reasonCode, RequestStatus status, Long processedBy, Instant processedAt,
                              String rejectReason, Instant createdAt) {
    }

    @Transactional
    public RequestView create(long userId, RequestForm f) {
        Member m = members.require(userId);
        if (f.type() == RequestType.WISH_DUTY && (f.duty() == null || !f.duty().isWork()))
            throw ApiException.badRequest("희망 근무는 D/E/N 중 하나여야 합니다");
        if (f.type() == RequestType.WISH_OFF && !REASON_CODES.contains(f.reasonCode()))
            throw ApiException.badRequest("사유 코드: " + REASON_CODES);
        if (schedules.isLocked(m.wardId(), f.date())) throw ApiException.conflict("이미 확정된 월입니다");
        ShiftRequest r = new ShiftRequest();
        r.wardId = m.wardId();
        r.nurseId = m.nurseId();
        r.type = f.type();
        r.date = f.date();
        r.duty = f.type() == RequestType.WISH_DUTY ? f.duty() : null;
        r.reasonCode = f.reasonCode();
        return view(requests.save(r));
    }

    /** REQ-02: 근무표 확정 전까지 본인만 취소 */
    @Transactional
    public void cancel(long userId, long id) {
        Member m = members.require(userId);
        ShiftRequest r = requests.findByIdAndWardId(id, m.wardId())
                .filter(x -> x.nurseId == m.nurseId()).orElseThrow(ApiException::notFound);
        if (r.status != RequestStatus.PENDING && r.status != RequestStatus.APPROVED) throw ApiException.conflict("취소할 수 없는 상태입니다");
        if (schedules.isLocked(m.wardId(), r.date)) throw ApiException.conflict("확정된 근무표의 신청은 취소할 수 없습니다");
        if (r.status == RequestStatus.APPROVED && r.type == RequestType.ANNUAL_LEAVE)
            schedules.syncLeave(m.wardId(), r.nurseId, r.date, false);
        r.status = RequestStatus.CANCELED;
    }

    @Transactional
    public RequestView decide(long userId, long id, boolean approve, String rejectReason) {
        Member m = members.requireHead(userId);
        ShiftRequest r = requests.findByIdAndWardId(id, m.wardId()).orElseThrow(ApiException::notFound);
        if (r.status != RequestStatus.PENDING) throw ApiException.conflict("이미 처리된 신청입니다");
        r.status = approve ? RequestStatus.APPROVED : RequestStatus.REJECTED;
        r.processedBy = userId;
        r.processedAt = Instant.now();
        r.rejectReason = approve ? null : rejectReason;
        if (approve && r.type == RequestType.ANNUAL_LEAVE) schedules.syncLeave(m.wardId(), r.nurseId, r.date, true);
        audit.log(m.wardId(), userId, approve ? "REQUEST_APPROVED" : "REQUEST_REJECTED", "request:" + r.id, RequestStatus.PENDING, r.status);
        nurses.findById(r.nurseId).ifPresent(n -> notifications.notifyUser(n.userId, com.yanggochi.nurs.domain.NotificationType.REQUEST_DECIDED,
                r.date + " " + r.type + " 신청이 " + (approve ? "승인" : "반려(" + rejectReason + ")") + "되었습니다"));
        return view(r);
    }

    /** REQ-07 본인 조회 (SEC-03 원칙 6: me) */
    public PageResponse<RequestView> mine(long userId, String ym, RequestType type, RequestStatus status, int page, int size) {
        Member m = members.require(userId);
        return search(m, m.nurseId(), ym, type, status, page, size);
    }

    /** REQ-07 관리 조회. status 기본 PENDING */
    public PageResponse<RequestView> ward(long userId, Long nurseId, String ym, RequestType type, RequestStatus status, int page, int size) {
        Member m = members.requireHead(userId);
        return search(m, nurseId, ym, type, status == null ? RequestStatus.PENDING : status, page, size);
    }

    private PageResponse<RequestView> search(Member m, Long nurseId, String ym, RequestType type, RequestStatus status, int page, int size) {
        Specification<ShiftRequest> s = (r, q, cb) -> cb.equal(r.get("wardId"), m.wardId());
        if (nurseId != null) s = s.and((r, q, cb) -> cb.equal(r.get("nurseId"), nurseId));
        if (type != null) s = s.and((r, q, cb) -> cb.equal(r.get("type"), type));
        if (status != null) s = s.and((r, q, cb) -> cb.equal(r.get("status"), status));
        if (ym != null) {
            YearMonth month = YearMonth.parse(ym);
            s = s.and((r, q, cb) -> cb.between(r.get("date"), month.atDay(1), month.atEndOfMonth()));
        }
        return PageResponse.of(requests.findAll(s, PageRequest.of(page, Math.min(size, 100), Sort.by(Sort.Direction.DESC, "createdAt")))
                .map(this::view));
    }

    private RequestView view(ShiftRequest r) {
        return new RequestView(r.id, r.nurseId, nurses.findById(r.nurseId).map(n -> n.name).orElse(null), r.type, r.date,
                r.duty, r.reasonCode, r.status, r.processedBy, r.processedAt, r.rejectReason, r.createdAt);
    }
}
