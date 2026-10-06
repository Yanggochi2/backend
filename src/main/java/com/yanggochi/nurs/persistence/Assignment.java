package com.yanggochi.nurs.persistence;

import com.yanggochi.nurs.domain.Duty;
import jakarta.persistence.*;

import java.time.LocalDate;

@Entity
@Table(uniqueConstraints = @UniqueConstraint(columnNames = {"scheduleId", "nurseId", "date"}))
public class Assignment {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    public Long id;
    public Long scheduleId;
    public Long nurseId;
    public LocalDate date;
    @Enumerated(EnumType.STRING)
    public Duty duty;

    public Assignment() {
    }

    public Assignment(Long scheduleId, Long nurseId, LocalDate date, Duty duty) {
        this.scheduleId = scheduleId;
        this.nurseId = nurseId;
        this.date = date;
        this.duty = duty;
    }
}
