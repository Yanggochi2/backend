package com.yanggochi.nurs.business;

import com.yanggochi.nurs.persistence.AuditLog;
import com.yanggochi.nurs.persistence.AuditLogRepository;
import com.yanggochi.nurs.persistence.UserRepository;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;

import java.time.Instant;

/** SEC-02 */
@Service
public class AuditService {
    private final AuditLogRepository logs;
    private final UserRepository users;
    private final MemberService members;

    public AuditService(AuditLogRepository logs, UserRepository users, MemberService members) {
        this.logs = logs;
        this.users = users;
        this.members = members;
    }

    public record AuditView(long id, Instant at, Long actorUserId, String actorName, String action, String target,
                            String before, String after) {
    }

    public void log(Long wardId, Long actorUserId, String action, String target, Object before, Object after) {
        AuditLog l = new AuditLog();
        l.wardId = wardId;
        l.actorUserId = actorUserId;
        l.action = action;
        l.target = target;
        l.beforeValue = before == null ? null : String.valueOf(before);
        l.afterValue = after == null ? null : String.valueOf(after);
        logs.save(l);
    }

    public PageResponse<AuditView> search(long userId, Instant from, Instant to, Long actorUserId, String action, int page, int size) {
        Member m = members.requireHead(userId);
        Specification<AuditLog> s = (r, q, cb) -> cb.equal(r.get("wardId"), m.wardId());
        if (from != null) s = s.and((r, q, cb) -> cb.greaterThanOrEqualTo(r.get("at"), from));
        if (to != null) s = s.and((r, q, cb) -> cb.lessThan(r.get("at"), to));
        if (actorUserId != null) s = s.and((r, q, cb) -> cb.equal(r.get("actorUserId"), actorUserId));
        if (action != null) s = s.and((r, q, cb) -> cb.equal(r.get("action"), action));
        var p = logs.findAll(s, PageRequest.of(page, Math.min(size, 100), Sort.by(Sort.Direction.DESC, "at")));
        return PageResponse.of(p.map(l -> new AuditView(l.id, l.at, l.actorUserId,
                l.actorUserId == null ? null : users.findById(l.actorUserId).map(u -> u.name).orElse(null),
                l.action, l.target, l.beforeValue, l.afterValue)));
    }
}
