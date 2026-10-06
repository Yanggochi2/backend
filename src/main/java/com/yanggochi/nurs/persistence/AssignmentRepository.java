package com.yanggochi.nurs.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface AssignmentRepository extends JpaRepository<Assignment, Long> {
    List<Assignment> findByScheduleId(Long scheduleId);

    List<Assignment> findByScheduleIdAndDate(Long scheduleId, java.time.LocalDate date);

    void deleteByScheduleId(Long scheduleId);
}
