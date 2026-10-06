package com.yanggochi.nurs.presentation;

import com.yanggochi.nurs.business.ExcelService;
import com.yanggochi.nurs.business.ScheduleService;
import com.yanggochi.nurs.domain.Violation;
import jakarta.validation.Valid;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

import static com.yanggochi.nurs.presentation.WebConfig.USER_ID;

/** {ym} = yyyy-MM. 병동은 세션에서 판정 */
@RestController
@RequestMapping("/api/schedules/{ym}")
public class ScheduleController {
    private final ScheduleService schedules;
    private final ExcelService excel;

    public ScheduleController(ScheduleService schedules, ExcelService excel) {
        this.schedules = schedules;
        this.excel = excel;
    }

    @GetMapping("/export")
    public ResponseEntity<byte[]> export(@SessionAttribute(USER_ID) Long userId, @PathVariable String ym) {
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"schedule-" + ym + ".xlsx\"")
                .header(HttpHeaders.CONTENT_TYPE, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                .body(excel.export(userId, ym));
    }

    @PostMapping("/import/preview")
    public ExcelService.ImportPreview importPreview(@SessionAttribute(USER_ID) Long userId, @PathVariable String ym,
                                                    @RequestParam MultipartFile file) throws IOException {
        return excel.preview(userId, ym, file.getInputStream());
    }

    @PostMapping("/import/apply")
    public List<Violation> importApply(@SessionAttribute(USER_ID) Long userId, @PathVariable String ym,
                                       @Valid @RequestBody List<ScheduleService.CellEdit> body) {
        return excel.apply(userId, ym, body);
    }

    public record OffTargetBody(Integer offTarget) {
    }

    public record GenerateBody(@Valid List<ScheduleService.FixedCell> fixed) {
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ScheduleService.ScheduleView create(@SessionAttribute(USER_ID) Long userId, @PathVariable String ym) {
        return schedules.create(userId, ym);
    }

    @GetMapping
    public ScheduleService.ScheduleView get(@SessionAttribute(USER_ID) Long userId, @PathVariable String ym) {
        return schedules.get(userId, ym);
    }

    @PatchMapping("/cells")
    public List<Violation> editCells(@SessionAttribute(USER_ID) Long userId, @PathVariable String ym,
                                     @Valid @RequestBody List<ScheduleService.CellEdit> body) {
        return schedules.editCells(userId, ym, body);
    }

    @GetMapping("/violations")
    public List<Violation> violations(@SessionAttribute(USER_ID) Long userId, @PathVariable String ym) {
        return schedules.violations(userId, ym);
    }

    @PostMapping("/generate")
    public ScheduleService.GenerateResult generate(@SessionAttribute(USER_ID) Long userId, @PathVariable String ym,
                                                   @RequestBody(required = false) GenerateBody body) {
        return schedules.generate(userId, ym, body == null ? null : body.fixed());
    }

    /** SCH-13 편집 잠금 획득. force=true면 강제로 가져온다 */
    @PostMapping("/lock")
    public ScheduleService.LockView lock(@SessionAttribute(USER_ID) Long userId, @PathVariable String ym,
                                        @RequestParam(defaultValue = "false") boolean force) {
        return schedules.lock(userId, ym, force);
    }

    @DeleteMapping("/lock")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void unlock(@SessionAttribute(USER_ID) Long userId, @PathVariable String ym) {
        schedules.unlock(userId, ym);
    }

    @PostMapping("/confirm")
    public List<Violation> confirm(@SessionAttribute(USER_ID) Long userId, @PathVariable String ym) {
        return schedules.confirm(userId, ym);
    }

    @PostMapping("/unconfirm")
    public void unconfirm(@SessionAttribute(USER_ID) Long userId, @PathVariable String ym,
                          @Valid @RequestBody ScheduleService.Unconfirm body) {
        schedules.unconfirm(userId, ym, body.reason());
    }

    @PutMapping("/off-target")
    public Map<String, Integer> offTarget(@SessionAttribute(USER_ID) Long userId, @PathVariable String ym,
                                          @RequestBody OffTargetBody body) {
        return Map.of("offTarget", schedules.setOffTarget(userId, ym, body.offTarget()));
    }
}
