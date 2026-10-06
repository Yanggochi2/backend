package com.yanggochi.nurs.persistence;

import com.yanggochi.nurs.domain.Duty;
import com.yanggochi.nurs.domain.RequestStatus;
import com.yanggochi.nurs.domain.RequestType;
import jakarta.persistence.*;

import java.time.Instant;
import java.time.LocalDate;

/** REQ. 연차·희망오프·희망근무 통합 신청 */
@Entity
public class ShiftRequest {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    public Long id;
    public Long wardId;
    public Long nurseId;
    @Enumerated(EnumType.STRING)
    public RequestType type;
    public LocalDate date;
    /** WISH_DUTY일 때 희망 듀티 */
    @Enumerated(EnumType.STRING)
    public Duty duty;
    public String reasonCode;
    @Enumerated(EnumType.STRING)
    public RequestStatus status = RequestStatus.PENDING;
    public Long processedBy;
    public Instant processedAt;
    public String rejectReason;
    public Instant createdAt = Instant.now();
}
