package com.yanggochi.nurs.presentation;

import com.yanggochi.nurs.business.ApiException;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.LinkedHashMap;
import java.util.Map;

@RestControllerAdvice
public class ErrorHandler {
    @ExceptionHandler(ApiException.class)
    ResponseEntity<Map<String, Object>> api(ApiException e) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("message", e.getMessage());
        if (e.detail != null) body.put("detail", e.detail);
        return ResponseEntity.status(e.status).body(body);
    }
}
