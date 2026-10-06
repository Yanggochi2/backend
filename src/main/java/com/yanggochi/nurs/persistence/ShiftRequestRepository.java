package com.yanggochi.nurs.persistence;

import com.yanggochi.nurs.domain.RequestStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;

import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

public interface ShiftRequestRepository extends JpaRepository<ShiftRequest, Long>, JpaSpecificationExecutor<ShiftRequest> {
    Optional<ShiftRequest> findByIdAndWardId(Long id, Long wardId);

    boolean existsByNurseIdAndDateAndStatusIn(Long nurseId, LocalDate date, List<RequestStatus> statuses);

    List<ShiftRequest> findByWardIdAndStatusAndDateBetween(Long wardId, RequestStatus status, LocalDate from, LocalDate to);
}
