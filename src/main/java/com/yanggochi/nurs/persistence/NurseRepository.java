package com.yanggochi.nurs.persistence;

import com.yanggochi.nurs.domain.NurseStatus;
import com.yanggochi.nurs.domain.Role;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface NurseRepository extends JpaRepository<Nurse, Long> {
    List<Nurse> findByWardId(Long wardId);

    Optional<Nurse> findByIdAndWardId(Long id, Long wardId);

    Optional<Nurse> findFirstByUserIdAndStatusNot(Long userId, NurseStatus status);

    long countByWardIdAndRoleAndStatusNot(Long wardId, Role role, NurseStatus status);
}
