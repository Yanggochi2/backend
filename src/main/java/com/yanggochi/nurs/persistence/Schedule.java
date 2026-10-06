package com.yanggochi.nurs.persistence;

import com.yanggochi.nurs.domain.ScheduleStatus;
import jakarta.persistence.*;

@Entity
@Table(uniqueConstraints = @UniqueConstraint(columnNames = {"wardId", "yearMonth"}))
public class Schedule {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    public Long id;
    public Long wardId;
    /** yyyy-MM */
    public String yearMonth;
    @Enumerated(EnumType.STRING)
    public ScheduleStatus status = ScheduleStatus.DRAFT;
    /** RULE-04 수동 조정값. null이면 자동 계산 */
    public Integer offTarget;
    /** SCH-13 편집 잠금 보유자·마지막 활동 시각 */
    public Long lockedBy;
    public java.time.Instant lockedAt;
    @Version
    public long version;
}
