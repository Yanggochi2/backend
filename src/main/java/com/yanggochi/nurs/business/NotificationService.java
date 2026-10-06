package com.yanggochi.nurs.business;

import com.yanggochi.nurs.domain.NotificationType;
import com.yanggochi.nurs.domain.NurseStatus;
import com.yanggochi.nurs.domain.ScheduleStatus;
import com.yanggochi.nurs.persistence.*;
import org.springframework.data.domain.PageRequest;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.LocalDate;
import java.time.YearMonth;
import java.time.ZoneId;
import java.util.Set;

/**
 * REQ-06. 앱 내 알림함으로 저장한다.
 * ponytail: 웹 푸시(🔶)는 수단 확정 후 notifyUser 한 곳에 발송만 붙이면 된다
 */
@Service
public class NotificationService {
    private static final ZoneId SEOUL = ZoneId.of("Asia/Seoul");

    private final NotificationRepository notifications;
    private final UserRepository users;
    private final NurseRepository nurses;
    private final ScheduleRepository schedules;
    private final AssignmentRepository assignments;

    public NotificationService(NotificationRepository notifications, UserRepository users, NurseRepository nurses,
                               ScheduleRepository schedules, AssignmentRepository assignments) {
        this.notifications = notifications;
        this.users = users;
        this.nurses = nurses;
        this.schedules = schedules;
        this.assignments = assignments;
    }

    public record NotificationView(long id, NotificationType type, String message, Instant createdAt, boolean read) {
    }

    public record Inbox(long unread, PageResponse<NotificationView> page) {
    }

    public record Settings(Set<NotificationType> muted) {
    }

    void notifyUser(Long userId, NotificationType type, String message) {
        if (userId == null) return;
        if (users.findById(userId).map(u -> u.mutedNotifications.contains(type)).orElse(true)) return;
        Notification n = new Notification();
        n.userId = userId;
        n.type = type;
        n.message = message;
        notifications.save(n);
    }

    /** 병동의 계정 있는 재직 간호사 전원 */
    void notifyWard(long wardId, NotificationType type, String message) {
        nurses.findByWardId(wardId).stream()
                .filter(n -> n.userId != null && n.status != NurseStatus.RETIRED)
                .forEach(n -> notifyUser(n.userId, type, message));
    }

    public Inbox mine(long userId, int page, int size) {
        var p = notifications.findByUserIdOrderByCreatedAtDesc(userId, PageRequest.of(page, Math.min(size, 100)))
                .map(n -> new NotificationView(n.id, n.type, n.message, n.createdAt, n.readAt != null));
        return new Inbox(notifications.countByUserIdAndReadAtIsNull(userId), PageResponse.of(p));
    }

    @Transactional
    public void markRead(long userId, long id) {
        Notification n = notifications.findByIdAndUserId(id, userId).orElseThrow(ApiException::notFound);
        if (n.readAt == null) n.readAt = Instant.now();
    }

    public Settings settings(long userId) {
        return new Settings(Set.copyOf(users.findById(userId).orElseThrow(ApiException::notFound).mutedNotifications));
    }

    @Transactional
    public Settings updateSettings(long userId, Settings s) {
        User u = users.findById(userId).orElseThrow(ApiException::notFound);
        u.mutedNotifications.clear();
        if (s.muted() != null) u.mutedNotifications.addAll(s.muted());
        return new Settings(Set.copyOf(u.mutedNotifications));
    }

    /** 근무 전날 리마인드. 확정본 기준, 매일 18시(KST) */
    @Scheduled(cron = "${reminder.cron:0 0 18 * * *}", zone = "Asia/Seoul")
    @Transactional
    public void remindTomorrow() {
        LocalDate tomorrow = LocalDate.now(SEOUL).plusDays(1);
        for (Schedule s : schedules.findByYearMonthAndStatus(YearMonth.from(tomorrow).toString(), ScheduleStatus.CONFIRMED))
            for (Assignment a : assignments.findByScheduleIdAndDate(s.id, tomorrow))
                if (a.duty.isWork())
                    nurses.findById(a.nurseId).ifPresent(n ->
                            notifyUser(n.userId, NotificationType.DUTY_REMINDER, "내일(" + tomorrow + ") " + a.duty + " 근무입니다"));
    }
}
