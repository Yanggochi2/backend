package com.yanggochi.nurs.business;

import com.yanggochi.nurs.domain.RequestStatus;
import com.yanggochi.nurs.domain.Role;
import com.yanggochi.nurs.persistence.*;
import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** AUTH-01·05 */
@Service
public class AuthService {
    private final UserRepository users;
    private final WardRepository wards;
    private final JoinRequestRepository joins;
    private final MemberService members;
    private final AuditService audit;
    private final PasswordEncoder encoder;

    public AuthService(UserRepository users, WardRepository wards, JoinRequestRepository joins, MemberService members,
                       AuditService audit, PasswordEncoder encoder) {
        this.users = users;
        this.wards = wards;
        this.joins = joins;
        this.members = members;
        this.audit = audit;
        this.encoder = encoder;
    }

    /** role·wardId 필드는 의도적으로 없다. 클라이언트가 보내도 바인딩되지 않는다 (AUTH-01 보안). */
    public record Signup(@NotBlank String name, @NotBlank @Email String email,
                         @Pattern(regexp = "^(?=.*[A-Za-z])(?=.*\\d).{8,}$", message = "8자 이상, 영문+숫자") String password,
                         @AssertTrue(message = "약관 동의가 필요합니다") boolean agreeTerms) {
    }

    public record Login(@NotBlank String email, @NotBlank String password) {
    }

    public record Me(long userId, String name, String email, Long wardId, String wardName, Long nurseId, Role role,
                     boolean joinPending) {
    }

    @Transactional
    public long signup(Signup s) {
        String email = s.email().trim().toLowerCase();
        if (users.existsByEmail(email)) throw ApiException.conflict("이미 가입된 이메일입니다");
        User u = new User();
        u.email = email;
        u.name = s.name().trim();
        u.passwordHash = encoder.encode(s.password());
        return users.save(u).id;
    }

    @Transactional
    public long login(Login l) {
        User u = users.findByEmail(l.email().trim().toLowerCase())
                .filter(x -> encoder.matches(l.password(), x.passwordHash))
                .orElseThrow(() -> ApiException.unauthorized("이메일 또는 비밀번호가 올바르지 않습니다"));
        audit.log(wardOf(u.id), u.id, "LOGIN", "user:" + u.id, null, null);
        return u.id;
    }

    @Transactional
    public void logout(long userId) {
        audit.log(wardOf(userId), userId, "LOGOUT", "user:" + userId, null, null);
    }

    public Me me(long userId) {
        User u = users.findById(userId).orElseThrow(() -> ApiException.unauthorized("다시 로그인하세요"));
        var n = members.membership(userId);
        return new Me(u.id, u.name, u.email,
                n.map(x -> x.wardId).orElse(null),
                n.flatMap(x -> wards.findById(x.wardId)).map(w -> w.name).orElse(null),
                n.map(x -> x.id).orElse(null),
                n.map(x -> x.role).orElse(null),
                joins.existsByUserIdAndStatus(userId, RequestStatus.PENDING));
    }

    private Long wardOf(long userId) {
        return members.membership(userId).map(n -> n.wardId).orElse(null);
    }
}
