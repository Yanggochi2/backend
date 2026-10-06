package com.yanggochi.nurs.persistence;

import com.yanggochi.nurs.domain.RequestStatus;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface JoinRequestRepository extends JpaRepository<JoinRequest, Long> {
    List<JoinRequest> findByWardIdAndStatus(Long wardId, RequestStatus status);

    Optional<JoinRequest> findByIdAndWardId(Long id, Long wardId);

    Optional<JoinRequest> findFirstByUserIdOrderByIdDesc(Long userId);

    Optional<JoinRequest> findFirstByUserIdAndStatus(Long userId, RequestStatus status);

    boolean existsByUserIdAndStatus(Long userId, RequestStatus status);
}
