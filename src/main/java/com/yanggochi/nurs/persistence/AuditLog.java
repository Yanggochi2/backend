package com.yanggochi.nurs.persistence;

import jakarta.persistence.*;

import java.time.Instant;

/** SEC-02. 조회 전용. 수정·삭제 경로 없음 */
@Entity
@Table(indexes = @Index(columnList = "wardId, at"))
public class AuditLog {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    public Long id;
    public Long wardId;
    public Long actorUserId;
    public String action;
    public String target;
    @Column(length = 2000)
    public String beforeValue;
    @Column(length = 2000)
    public String afterValue;
    public Instant at = Instant.now();
}
