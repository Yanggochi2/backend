package com.yanggochi.nurs.persistence;

import jakarta.persistence.*;

import java.time.LocalDate;

/** RULE-03 공휴일 (병동 단위) */
@Entity
@Table(uniqueConstraints = @UniqueConstraint(columnNames = {"wardId", "date"}))
public class Holiday {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    public Long id;
    public Long wardId;
    public LocalDate date;
    public String name;
}
