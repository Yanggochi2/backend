package com.yanggochi.nurs.persistence;

import jakarta.persistence.*;

import java.time.Instant;

@Entity
@Table(name = "users")
public class User {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    public Long id;
    @Column(nullable = false, unique = true)
    public String email;
    @Column(nullable = false)
    public String passwordHash;
    @Column(nullable = false)
    public String name;
    public Instant createdAt = Instant.now();
    /** REQ-06 종류별 off */
    @ElementCollection(fetch = FetchType.EAGER)
    @Enumerated(EnumType.STRING)
    public java.util.Set<com.yanggochi.nurs.domain.NotificationType> mutedNotifications = new java.util.HashSet<>();
}
