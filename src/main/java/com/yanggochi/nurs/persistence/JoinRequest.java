package com.yanggochi.nurs.persistence;

import com.yanggochi.nurs.domain.RequestStatus;
import jakarta.persistence.*;

import java.time.Instant;

/** AUTH-03 가입 신청 */
@Entity
public class JoinRequest {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    public Long id;
    public Long wardId;
    public Long userId;
    @Enumerated(EnumType.STRING)
    public RequestStatus status = RequestStatus.PENDING;
    public Instant createdAt = Instant.now();
}
