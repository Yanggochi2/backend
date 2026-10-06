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
import java.util.List;

/** AUTH-00·03·04·06·07 */
@Service
public class WardService {
    private static final String CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

    private final WardRepository wards;
    private final NurseRepository nurses;
    private final UserRepository users;
    private final JoinRequestRepository joins;
    private final MemberService members;
    private final AuditService audit;
    private final NotificationService notifications;
    private final SecureRandom random = new SecureRandom();
    /** AUTH-03 코드 무차별 대입 방지: 계정당 시간당 10회 (🔶 권장값) */
    private final RateLimiter joinAttempts = new RateLimiter(10, java.time.Duration.ofHours(1));

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

    /** candidates: 이름이 같고 계정이 없는 기존 간호사. 승인 시 nurseId로 지정하면 그 행에 계정을 연결한다 */
    public record JoinRequestView(long id, long userId, String name, String email, Instant createdAt, List<Long> candidates) {
    }

    /** 신청자 본인이 보는 가입 신청 상태 (승인 대기 화면용) */
    public record MyJoinView(long id, RequestStatus status, String wardName, String hospital, Instant createdAt,
                             Instant decidedAt, String rejectReason) {
    }

    public enum TransferMode { GRANT, TRANSFER }

    public WardView get(long userId) {
        Member m = members.require(userId);
        return view(wards.findById(m.wardId()).orElseThrow(ApiException::notFound), m.isHead());
    }

    @Transactional
    public WardView create(long userId, CreateWard req) {
        if (members.membership(userId).isPresent()) throw ApiException.conflict("이미 소속된 병동이 있습니다");
        // 대기 중인 신청이 나중에 승인되면 두 병동에 소속되므로 먼저 취소하게 한다
        if (joins.existsByUserIdAndStatus(userId, RequestStatus.PENDING))
            throw ApiException.conflict("승인 대기 중인 가입 신청을 먼저 취소하세요");
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

    /** AUTH-03. 신청 결과로 어느 병동에 신청됐는지 돌려준다 (승인 대기 화면에 표시) */
    @Transactional
    public MyJoinView join(long userId, String code) {
        joinAttempts.check(userId);
        joinAttempts.record(userId);
        if (members.membership(userId).isPresent()) throw ApiException.conflict("이미 소속된 병동이 있습니다");
        if (joins.existsByUserIdAndStatus(userId, RequestStatus.PENDING)) throw ApiException.conflict("승인 대기 중인 신청이 있습니다");
        String c = code == null ? "" : code.trim().toUpperCase();
        if (!c.matches("[A-Z0-9]{6,8}")) throw ApiException.badRequest("병동 코드는 영문·숫자 6~8자입니다");
        Ward w = wards.findByCode(c).orElseThrow(() -> ApiException.badRequest("유효하지 않은 병동 코드입니다"));
        JoinRequest j = new JoinRequest();
        j.wardId = w.id;
        j.userId = userId;
        joins.save(j);
        audit.log(w.id, userId, "JOIN_REQUESTED", "user:" + userId, null, null);
        return myView(j, w);
    }

    /** 가장 최근 가입 신청 (대기·승인·반려·취소) */
    public MyJoinView myJoin(long userId) {
        JoinRequest j = joins.findFirstByUserIdOrderByIdDesc(userId).orElseThrow(ApiException::notFound);
        return myView(j, wards.findById(j.wardId).orElseThrow(ApiException::notFound));
    }

    /** 승인 대기 중인 신청을 본인이 취소한다 (코드를 잘못 입력한 경우 등) */
    @Transactional
    public void cancelJoin(long userId) {
        JoinRequest j = joins.findFirstByUserIdAndStatus(userId, RequestStatus.PENDING).orElseThrow(ApiException::notFound);
        j.status = RequestStatus.CANCELED;
        j.decidedAt = Instant.now();
        audit.log(j.wardId, userId, "JOIN_CANCELED", "user:" + userId, null, null);
    }

    private static MyJoinView myView(JoinRequest j, Ward w) {
        return new MyJoinView(j.id, j.status, w.name, w.hospital, j.createdAt, j.decidedAt, j.rejectReason);
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
        List<Nurse> unlinked = nurses.findByWardId(m.wardId()).stream()
                .filter(n -> n.userId == null && n.status != NurseStatus.RETIRED).toList();
        return joins.findByWardIdAndStatus(m.wardId(), RequestStatus.PENDING).stream()
                .map(j -> {
                    User u = users.findById(j.userId).orElseThrow();
                    List<Long> candidates = unlinked.stream().filter(n -> n.name.equals(u.name)).map(n -> n.id).toList();
                    return new JoinRequestView(j.id, u.id, u.name, u.email, j.createdAt, candidates);
                }).toList();
    }

    /**
     * AUTH-06. 승인으로 부여되는 역할은 NURSE 고정.
     * linkNurseId가 있으면 수간호사가 미리 등록한 (계정 없는) 간호사 행에 계정을 연결한다. 같은 사람이 두 행이 되지 않게
     */
    @Transactional
    public void decideJoin(long userId, long joinId, boolean approve, Long linkNurseId, String rejectReason) {
        Member m = members.requireHead(userId);
        JoinRequest j = joins.findByIdAndWardId(joinId, m.wardId()).orElseThrow(ApiException::notFound);
        if (j.status != RequestStatus.PENDING) throw ApiException.conflict("이미 처리된 신청입니다");
        if (approve) {
            if (members.membership(j.userId).isPresent()) throw ApiException.conflict("이미 다른 병동에 소속된 사용자입니다");
            if (linkNurseId != null) {
                Nurse n = nurses.findByIdAndWardId(linkNurseId, m.wardId()).orElseThrow(ApiException::notFound);
                if (n.userId != null || n.status == NurseStatus.RETIRED)
                    throw ApiException.conflict("계정이 없는 재직 간호사에게만 연결할 수 있습니다");
                n.userId = j.userId;
                n.role = Role.NURSE;
            } else {
                Nurse n = new Nurse();
                n.wardId = m.wardId();
                n.userId = j.userId;
                n.name = users.findById(j.userId).orElseThrow().name;
                n.joinedAt = LocalDate.now();
                n.affiliationStart = LocalDate.now();
                nurses.save(n);
            }
        }
        j.status = approve ? RequestStatus.APPROVED : RequestStatus.REJECTED;
        j.decidedAt = Instant.now();
        j.decidedBy = userId;
        j.rejectReason = approve ? null : rejectReason;
        audit.log(m.wardId(), userId, approve ? "JOIN_APPROVED" : "JOIN_REJECTED", "user:" + j.userId, null,
                linkNurseId == null ? null : "linked nurse:" + linkNurseId);
        Ward w = wards.findById(m.wardId()).orElseThrow();
        if (approve)
            notifications.notifyUser(j.userId, com.yanggochi.nurs.domain.NotificationType.JOIN_APPROVED, w.name + " 가입이 승인되었습니다");
        else
            notifications.notifyUser(j.userId, com.yanggochi.nurs.domain.NotificationType.JOIN_REJECTED,
                    w.name + " 가입이 반려되었습니다" + (rejectReason == null || rejectReason.isBlank() ? "" : ". 사유: " + rejectReason));
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
