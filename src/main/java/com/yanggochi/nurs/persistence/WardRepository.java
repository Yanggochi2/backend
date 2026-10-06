package com.yanggochi.nurs.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface WardRepository extends JpaRepository<Ward, Long> {
    Optional<Ward> findByCode(String code);

    boolean existsByCode(String code);
}
