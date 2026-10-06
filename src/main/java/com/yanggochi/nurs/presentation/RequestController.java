package com.yanggochi.nurs.presentation;

import com.yanggochi.nurs.business.PageResponse;
import com.yanggochi.nurs.business.RequestService;
import com.yanggochi.nurs.domain.RequestStatus;
import com.yanggochi.nurs.domain.RequestType;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import static com.yanggochi.nurs.presentation.WebConfig.USER_ID;

@RestController
@RequestMapping("/api/requests")
public class RequestController {
    private final RequestService requests;

    public RequestController(RequestService requests) {
        this.requests = requests;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public RequestService.RequestView create(@SessionAttribute(USER_ID) Long userId, @Valid @RequestBody RequestService.RequestForm body) {
        return requests.create(userId, body);
    }

    @GetMapping("/me")
    public PageResponse<RequestService.RequestView> mine(@SessionAttribute(USER_ID) Long userId,
                                                         @RequestParam(required = false) String yearMonth,
                                                         @RequestParam(required = false) RequestType type,
                                                         @RequestParam(required = false) RequestStatus status,
                                                         @RequestParam(defaultValue = "0") int page,
                                                         @RequestParam(defaultValue = "20") int size) {
        return requests.mine(userId, yearMonth, type, status, page, size);
    }

    @GetMapping
    public PageResponse<RequestService.RequestView> ward(@SessionAttribute(USER_ID) Long userId,
                                                         @RequestParam(required = false) Long nurseId,
                                                         @RequestParam(required = false) String yearMonth,
                                                         @RequestParam(required = false) RequestType type,
                                                         @RequestParam(required = false) RequestStatus status,
                                                         @RequestParam(defaultValue = "0") int page,
                                                         @RequestParam(defaultValue = "20") int size) {
        return requests.ward(userId, nurseId, yearMonth, type, status, page, size);
    }

    @DeleteMapping("/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void cancel(@SessionAttribute(USER_ID) Long userId, @PathVariable long id) {
        requests.cancel(userId, id);
    }

    @PostMapping("/{id}/approve")
    public RequestService.RequestView approve(@SessionAttribute(USER_ID) Long userId, @PathVariable long id) {
        return requests.decide(userId, id, true, null);
    }

    @PostMapping("/{id}/reject")
    public RequestService.RequestView reject(@SessionAttribute(USER_ID) Long userId, @PathVariable long id,
                                             @Valid @RequestBody RequestService.Reject body) {
        return requests.decide(userId, id, false, body.reason());
    }
}
