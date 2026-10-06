package com.yanggochi.nurs.business;

import com.yanggochi.nurs.domain.NurseStatus;
import com.yanggochi.nurs.domain.RequestStatus;
import com.yanggochi.nurs.domain.Role;
import com.yanggochi.nurs.domain.Rules;
import com.yanggochi.nurs.persistence.*;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.SecureRandom;
import java.time.Instant;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** AUTH-00·03·04·06·07 */
@Service
public class WardService {
    private static final String CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    private static final int JOIN_ATTEMPTS_PER_HOUR = 10;

    private final WardRepository wards;
    private final NurseRepository nurses;
    private final UserRepository users;
    private final JoinRequestRepository joins;
    private final MemberService members;
    private final AuditService audit;
    private final NotificationService notifications;
    private final SecureRandom random = new SecureRandom();
    // ponytail: 인메모리 시도 횟수 제한. 서버 다중화 시 Redis 등 공유 저장소로
    private final Map<Long, Deque<Instant>> joinAttempts = new ConcurrentHashMap<>();

    public WardService(WardRepository wards, NurseRepository nurses, UserRepository users, JoinRequestRepository joins,
                       MemberService members, AuditService audit, NotificationService notifications) {
        this.notifications = notifications;
        this.wards = wards;
        this.nurses = nurses;
        this.users = users;
        this.joins = joins;
        this.members = members;
        this.audit = audit;
    }

    public record CreateWard(@NotBlank String name, @NotBlank String hospital,
                             @Min(0) int requiredD, @Min(0) int requiredE, @Min(0) int requiredN,
                             @NotNull Rules.Preset preset) {
    }

    public record WardView(long id, String name, String hospital, String code, Rules rules) {
    }

    public record JoinRequestView(long id, long userId, String name, String email, Instant createdAt) {
    }

    public enum TransferMode { GRANT, TRANSFER }

    public WardView get(long userId) {
        Member m = members.require(userId);
        return view(wards.findById(m.wardId()).orElseThrow(ApiException::notFound), m.isHead());
    }

    @Transactional
    public WardView create(long userId, CreateWard req) {
        if (members.membership(userId).isPresent()) throw ApiException.conflict("이미 소속된 병동이 있습니다");
        User u = users.findById(userId).orElseThrow(ApiException::notFound);
        Ward w = new Ward();
        w.name = req.name();
        w.hospital = req.hospital();
        w.apply(Rules.preset(req.preset(), req.requiredD(), req.requiredE(), req.requiredN()));
        w.code = newCode();
        wards.save(w);

        Nurse head = new Nurse();
        head.wardId = w.id;
        head.userId = userId;
        head.name = u.name;
        head.role = Role.HEAD_NURSE;
        head.joinedAt = LocalDate.now();
        head.affiliationStart = LocalDate.now();
        nurses.save(head);
        audit.log(w.id, userId, "WARD_CREATED", "ward:" + w.id, null, w.name);
        audit.log(w.id, userId, "CODE_ISSUED", "ward:" + w.id, null, null);
        return view(w, true);
    }

    @Transactional
    public void join(long userId, String code) {
        rateLimit(userId);
        if (members.membership(userId).isPresent()) throw ApiException.conflict("이미 소속된 병동이 있습니다");
        if (joins.existsByUserIdAndStatus(userId, RequestStatus.PENDING)) throw ApiException.conflict("승인 대기 중인 신청이 있습니다");
        Ward w = wards.findByCode(code == null ? "" : code.trim().toUpperCase())
                .orElseThrow(() -> ApiException.badRequest("유효하지 않은 병동 코드입니다"));
        JoinRequest j = new JoinRequest();
        j.wardId = w.id;
        j.userId = userId;
        joins.save(j);
        audit.log(w.id, userId, "JOIN_REQUESTED", "user:" + userId, null, null);
    }

    @Transactional
    public String reissueCode(long userId) {
        Member m = members.requireHead(userId);
        Ward w = wards.findById(m.wardId()).orElseThrow(ApiException::notFound);
        w.code = newCode();
        audit.log(w.id, userId, "CODE_REISSUED", "ward:" + w.id, null, null);
        return w.code;
    }

    public List<JoinRequestView> pendingJoins(long userId) {
        Member m = members.requireHead(userId);
        return joins.findByWardIdAndStatus(m.wardId(), RequestStatus.PENDING).stream()
                .map(j -> {
                    User u = users.findById(j.userId).orElseThrow();
                    return new JoinRequestView(j.id, u.id, u.name, u.email, j.createdAt);
                }).toList();
    }

    /** AUTH-06. 승인으로 부여되는 역할은 NURSE 고정 */
    @Transactional
    public void decideJoin(long userId, long joinId, boolean approve) {
        Member m = members.requireHead(userId);
        JoinRequest j = joins.findByIdAndWardId(joinId, m.wardId()).orElseThrow(ApiException::notFound);
        if (j.status != RequestStatus.PENDING) throw ApiException.conflict("이미 처리된 신청입니다");
        if (approve) {
            if (members.membership(j.userId).isPresent()) throw ApiException.conflict("이미 다른 병동에 소속된 사용자입니다");
            Nurse n = new Nurse();
            n.wardId = m.wardId();
            n.userId = j.userId;
            n.name = users.findById(j.userId).orElseThrow().name;
            n.joinedAt = LocalDate.now();
            n.affiliationStart = LocalDate.now();
            nurses.save(n);
        }
        j.status = approve ? RequestStatus.APPROVED : RequestStatus.REJECTED;
        audit.log(m.wardId(), userId, approve ? "JOIN_APPROVED" : "JOIN_REJECTED", "user:" + j.userId, null, null);
        if (approve) notifications.notifyUser(j.userId, com.yanggochi.nurs.domain.NotificationType.JOIN_APPROVED, "병동 가입이 승인되었습니다");
    }

    /** AUTH-07 */
    @Transactional
    public void transferHead(long userId, long nurseId, TransferMode mode) {
        Member m = members.requireHead(userId);
        Nurse target = nurses.findByIdAndWardId(nurseId, m.wardId()).orElseThrow(ApiException::notFound);
        if (target.userId == null || target.status == NurseStatus.RETIRED)
            throw ApiException.badRequest("계정이 연결된 재직 간호사에게만 부여할 수 있습니다");
        if (target.id == m.nurseId()) throw ApiException.badRequest("본인에게는 부여할 수 없습니다");
        target.role = Role.HEAD_NURSE;
        if (mode == TransferMode.TRANSFER) nurses.findById(m.nurseId()).orElseThrow().role = Role.NURSE;
        audit.log(m.wardId(), userId, "HEAD_" + mode, "nurse:" + nurseId, null, null);
    }

    private void rateLimit(long userId) {
        Deque<Instant> q = joinAttempts.computeIfAbsent(userId, k -> new ArrayDeque<>());
        synchronized (q) {
            Instant cutoff = Instant.now().minus(1, ChronoUnit.HOURS);
            while (!q.isEmpty() && q.peekFirst().isBefore(cutoff)) q.pollFirst();
            if (q.size() >= JOIN_ATTEMPTS_PER_HOUR) throw new ApiException(429, "잠시 후 다시 시도하세요", null);
            q.addLast(Instant.now());
        }
    }

    private String newCode() {
        String c;
        do {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < 8; i++) sb.append(CODE_CHARS.charAt(random.nextInt(CODE_CHARS.length())));
            c = sb.toString();
        } while (wards.existsByCode(c));
        return c;
    }

    private WardView view(Ward w, boolean head) {
        return new WardView(w.id, w.name, w.hospital, head ? w.code : null, w.rules());
    }
}
