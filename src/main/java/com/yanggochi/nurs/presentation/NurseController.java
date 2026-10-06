package com.yanggochi.nurs.presentation;

import com.yanggochi.nurs.business.NurseExcelService;
import com.yanggochi.nurs.business.NurseService;
import com.yanggochi.nurs.business.PageResponse;
import com.yanggochi.nurs.domain.DutyRole;
import com.yanggochi.nurs.domain.NurseStatus;
import com.yanggochi.nurs.domain.Role;
import jakarta.validation.Valid;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.List;
import org.springframework.web.bind.annotation.*;

import static com.yanggochi.nurs.presentation.WebConfig.USER_ID;

@RestController
@RequestMapping("/api/nurses")
public class NurseController {
    private final NurseService nurses;
    private final NurseExcelService excel;

    public NurseController(NurseService nurses, NurseExcelService excel) {
        this.nurses = nurses;
        this.excel = excel;
    }

    @GetMapping("/export")
    public ResponseEntity<byte[]> export(@SessionAttribute(USER_ID) Long userId,
                                         @RequestParam(defaultValue = "false") boolean includeRetired) {
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"nurses.xlsx\"")
                .header(HttpHeaders.CONTENT_TYPE, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                .body(excel.export(userId, includeRetired));
    }

    /** 일괄 등록 1단계: 검증 결과만, 저장 안 함 */
    @PostMapping("/import/preview")
    public List<NurseExcelService.NurseImportRow> importPreview(@SessionAttribute(USER_ID) Long userId,
                                                                @RequestParam MultipartFile file) throws IOException {
        return excel.preview(userId, file.getInputStream());
    }

    /** 일괄 등록 2단계: 미리보기의 form 배열을 그대로 보낸다. 하나라도 틀리면 전체 취소 */
    @PostMapping("/import/apply")
    @ResponseStatus(HttpStatus.CREATED)
    public List<NurseService.NurseView> importApply(@SessionAttribute(USER_ID) Long userId,
                                                    @RequestBody List<NurseService.NurseForm> body) {
        return excel.apply(userId, body);
    }

    @GetMapping
    public PageResponse<NurseService.NurseView> list(@SessionAttribute(USER_ID) Long userId,
                                                     @RequestParam(defaultValue = "false") boolean includeRetired,
                                                     @RequestParam(required = false) Role role,
                                                     @RequestParam(required = false) DutyRole dutyRole,
                                                     @RequestParam(required = false) NurseStatus status,
                                                     @RequestParam(defaultValue = "name") String sort,
                                                     @RequestParam(defaultValue = "0") int page,
                                                     @RequestParam(defaultValue = "50") int size) {
        return nurses.list(userId, includeRetired, role, dutyRole, status, sort, page, size);
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public NurseService.NurseResult register(@SessionAttribute(USER_ID) Long userId, @Valid @RequestBody NurseService.NurseForm body) {
        return nurses.register(userId, body);
    }

    @PutMapping("/{id}")
    public NurseService.NurseResult update(@SessionAttribute(USER_ID) Long userId, @PathVariable long id,
                                           @Valid @RequestBody NurseService.NurseForm body) {
        return nurses.update(userId, id, body);
    }

    @PostMapping("/{id}/retire")
    public NurseService.NurseResult retire(@SessionAttribute(USER_ID) Long userId, @PathVariable long id,
                                           @Valid @RequestBody NurseService.Retire body) {
        return nurses.retire(userId, id, body.affiliationEnd());
    }
}
