package com.yanggochi.nurs.presentation;

import com.yanggochi.nurs.business.NotificationService;
import org.springframework.web.bind.annotation.*;

import static com.yanggochi.nurs.presentation.WebConfig.USER_ID;

/** REQ-06. 본인 것만 (SEC-03 원칙 6) */
@RestController
@RequestMapping("/api/notifications/me")
public class NotificationController {
    private final NotificationService notifications;

    public NotificationController(NotificationService notifications) {
        this.notifications = notifications;
    }

    @GetMapping
    public NotificationService.Inbox mine(@SessionAttribute(USER_ID) Long userId,
                                          @RequestParam(defaultValue = "0") int page,
                                          @RequestParam(defaultValue = "20") int size) {
        return notifications.mine(userId, page, size);
    }

    @PostMapping("/{id}/read")
    public void read(@SessionAttribute(USER_ID) Long userId, @PathVariable long id) {
        notifications.markRead(userId, id);
    }

    @GetMapping("/settings")
    public NotificationService.Settings settings(@SessionAttribute(USER_ID) Long userId) {
        return notifications.settings(userId);
    }

    @PutMapping("/settings")
    public NotificationService.Settings updateSettings(@SessionAttribute(USER_ID) Long userId,
                                                       @RequestBody NotificationService.Settings body) {
        return notifications.updateSettings(userId, body);
    }
}
