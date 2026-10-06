package com.yanggochi.nurs.persistence;

import com.yanggochi.nurs.domain.ScheduleStatus;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface ScheduleRepository extends JpaRepository<Schedule, Long> {
    Optional<Schedule> findByWardIdAndYearMonth(Long wardId, String yearMonth);

    List<Schedule> findByYearMonthAndStatus(String yearMonth, ScheduleStatus status);

    List<Schedule> findByWardIdAndStatusIn(Long wardId, List<ScheduleStatus> statuses);
}
