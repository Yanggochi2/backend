package com.yanggochi.nurs.presentation;

import com.yanggochi.nurs.business.RuleService;
import com.yanggochi.nurs.business.WardService;
import com.yanggochi.nurs.domain.Rules;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.time.YearMonth;
import java.util.List;
import java.util.Map;

import static com.yanggochi.nurs.presentation.WebConfig.USER_ID;

/** 병동 ID는 경로에 없다. 항상 세션 사용자의 소속 병동이 대상 (SEC-03) */
@RestController
@RequestMapping("/api")
public class WardController {
    private final WardService wards;
    private final RuleService rules;

    public WardController(WardService wards, RuleService rules) {
        this.wards = wards;
        this.rules = rules;
    }

    public record JoinBody(@NotNull String code) {
    }

    public record TransferBody(@NotNull Long nurseId, @NotNull WardService.TransferMode mode) {
    }

    public record PresetBody(@NotNull Rules.Preset preset) {
    }

    @PostMapping("/wards")
    @ResponseStatus(HttpStatus.CREATED)
    public WardService.WardView create(@SessionAttribute(USER_ID) Long userId, @Valid @RequestBody WardService.CreateWard body) {
        return wards.create(userId, body);
    }

    @GetMapping("/ward")
    public WardService.WardView get(@SessionAttribute(USER_ID) Long userId) {
        return wards.get(userId);
    }

    @PostMapping("/ward/join")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public void join(@SessionAttribute(USER_ID) Long userId, @Valid @RequestBody JoinBody body) {
        wards.join(userId, body.code());
    }

    @PostMapping("/ward/code")
    public Map<String, String> reissueCode(@SessionAttribute(USER_ID) Long userId) {
        return Map.of("code", wards.reissueCode(userId));
    }

    @GetMapping("/ward/join-requests")
    public List<WardService.JoinRequestView> joinRequests(@SessionAttribute(USER_ID) Long userId) {
        return wards.pendingJoins(userId);
    }

    @PostMapping("/ward/join-requests/{id}/approve")
    public void approveJoin(@SessionAttribute(USER_ID) Long userId, @PathVariable long id) {
        wards.decideJoin(userId, id, true);
    }

    @PostMapping("/ward/join-requests/{id}/reject")
    public void rejectJoin(@SessionAttribute(USER_ID) Long userId, @PathVariable long id) {
        wards.decideJoin(userId, id, false);
    }

    @PostMapping("/ward/head-transfer")
    public void transferHead(@SessionAttribute(USER_ID) Long userId, @Valid @RequestBody TransferBody body) {
        wards.transferHead(userId, body.nurseId(), body.mode());
    }

    @GetMapping("/rules")
    public Rules getRules(@SessionAttribute(USER_ID) Long userId) {
        return rules.get(userId);
    }

    @PutMapping("/rules")
    public Rules updateRules(@SessionAttribute(USER_ID) Long userId, @Valid @RequestBody RuleService.RulesForm body) {
        return rules.update(userId, body);
    }

    @PostMapping("/rules/preset")
    public Rules applyPreset(@SessionAttribute(USER_ID) Long userId, @Valid @RequestBody PresetBody body) {
        return rules.applyPreset(userId, body.preset());
    }

    @GetMapping("/holidays")
    public List<RuleService.HolidayView> holidays(@SessionAttribute(USER_ID) Long userId, @RequestParam String yearMonth) {
        return rules.holidays(userId, YearMonth.parse(yearMonth));
    }

    @PostMapping("/holidays")
    @ResponseStatus(HttpStatus.CREATED)
    public RuleService.HolidayView addHoliday(@SessionAttribute(USER_ID) Long userId, @Valid @RequestBody RuleService.HolidayForm body) {
        return rules.addHoliday(userId, body);
    }

    @DeleteMapping("/holidays/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void deleteHoliday(@SessionAttribute(USER_ID) Long userId, @PathVariable long id) {
        rules.deleteHoliday(userId, id);
    }
}
