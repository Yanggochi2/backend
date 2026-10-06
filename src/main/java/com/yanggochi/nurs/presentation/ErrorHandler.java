package com.yanggochi.nurs.presentation;

import com.yanggochi.nurs.business.ApiException;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.orm.ObjectOptimisticLockingFailureException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.multipart.MaxUploadSizeExceededException;

import java.time.format.DateTimeParseException;
import java.util.LinkedHashMap;
import java.util.Map;

/** 모든 오류를 {message, detail?} 형식으로 응답한다. 예상 가능한 오류가 500으로 새지 않게 한다 */
@RestControllerAdvice
public class ErrorHandler {
    @ExceptionHandler(ApiException.class)
    ResponseEntity<Map<String, Object>> api(ApiException e) {
        return body(e.status, e.getMessage(), e.detail);
    }

    /** @Version 충돌: 다른 수간호사가 먼저 저장함 */
    @ExceptionHandler(ObjectOptimisticLockingFailureException.class)
    ResponseEntity<Map<String, Object>> optimisticLock(ObjectOptimisticLockingFailureException e) {
        return body(409, "다른 사용자가 먼저 수정했습니다. 새로고침 후 다시 시도하세요", null);
    }

    /** unique 제약 경쟁 (동시 가입 같은 이메일, 같은 달 근무표 동시 생성 등) */
    @ExceptionHandler(DataIntegrityViolationException.class)
    ResponseEntity<Map<String, Object>> conflict(DataIntegrityViolationException e) {
        return body(409, "이미 존재하는 데이터입니다", null);
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<Map<String, Object>> invalid(MethodArgumentNotValidException e) {
        Map<String, String> fields = new LinkedHashMap<>();
        e.getBindingResult().getFieldErrors().forEach(f -> fields.putIfAbsent(f.getField(), f.getDefaultMessage()));
        return body(400, "입력값이 올바르지 않습니다", fields);
    }

    @ExceptionHandler({HttpMessageNotReadableException.class, MethodArgumentTypeMismatchException.class, DateTimeParseException.class})
    ResponseEntity<Map<String, Object>> badRequest(Exception e) {
        return body(400, "요청 형식이 올바르지 않습니다", null);
    }

    @ExceptionHandler(MaxUploadSizeExceededException.class)
    ResponseEntity<Map<String, Object>> tooLarge(MaxUploadSizeExceededException e) {
        return body(413, "파일이 너무 큽니다 (최대 10MB)", null);
    }

    private static ResponseEntity<Map<String, Object>> body(int status, String message, Object detail) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("message", message);
        if (detail != null) body.put("detail", detail);
        return ResponseEntity.status(status).body(body);
    }
}
