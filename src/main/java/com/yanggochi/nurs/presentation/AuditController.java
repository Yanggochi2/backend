package com.yanggochi.nurs.presentation;

import com.yanggochi.nurs.business.AuditService;
import com.yanggochi.nurs.business.PageResponse;
import org.springframework.web.bind.annotation.*;

import java.time.Instant;

import static com.yanggochi.nurs.presentation.WebConfig.USER_ID;

@RestController
public class AuditController {
    private final AuditService audit;

    public AuditController(AuditService audit) {
        this.audit = audit;
    }

    @GetMapping("/api/audit-logs")
    public PageResponse<AuditService.AuditView> search(@SessionAttribute(USER_ID) Long userId,
                                                       @RequestParam(required = false) Instant from,
                                                       @RequestParam(required = false) Instant to,
                                                       @RequestParam(required = false) Long actorUserId,
                                                       @RequestParam(required = false) String action,
                                                       @RequestParam(defaultValue = "0") int page,
                                                       @RequestParam(defaultValue = "50") int size) {
        return audit.search(userId, from, to, actorUserId, action, page, size);
    }
}
