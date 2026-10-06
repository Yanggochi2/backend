package com.yanggochi.nurs.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

public interface HolidayRepository extends JpaRepository<Holiday, Long> {
    List<Holiday> findByWardIdAndDateBetweenOrderByDate(Long wardId, LocalDate from, LocalDate to);

    Optional<Holiday> findByIdAndWardId(Long id, Long wardId);
}
