package com.yanggochi.nurs.persistence;

import com.yanggochi.nurs.domain.*;
import jakarta.persistence.*;

import java.time.LocalDate;
import java.util.HashSet;
import java.util.Set;

/** COM-02. userId가 null이면 계정 없이 수간호사가 등록한 간호사 */
@Entity
public class Nurse {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    public Long id;
    @Column(nullable = false)
    public Long wardId;
    public Long userId;
    @Column(nullable = false)
    public String name;
    @Enumerated(EnumType.STRING)
    public Role role = Role.NURSE;
    @Enumerated(EnumType.STRING)
    public DutyRole dutyRole = DutyRole.GENERAL;
    @Enumerated(EnumType.STRING)
    public NurseStatus status = NurseStatus.ACTIVE;
    public LocalDate joinedAt;
    public int careerMonths;
    public int skillLevel = 1;
    public LocalDate affiliationStart;
    public LocalDate affiliationEnd;
    @ElementCollection(fetch = FetchType.EAGER)
    public Set<Long> preceptorOf = new HashSet<>();

    public NurseInfo info() {
        return new NurseInfo(id, name, dutyRole, status, affiliationStart, affiliationEnd, Set.copyOf(preceptorOf));
    }
}
