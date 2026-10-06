package com.yanggochi.nurs.persistence;

import com.yanggochi.nurs.domain.NotificationType;
import jakarta.persistence.*;

import java.time.Instant;

@Entity
@Table(indexes = @Index(columnList = "userId, createdAt"))
public class Notification {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    public Long id;
    public Long userId;
    @Enumerated(EnumType.STRING)
    public NotificationType type;
    public String message;
    public Instant createdAt = Instant.now();
    public Instant readAt;
}
