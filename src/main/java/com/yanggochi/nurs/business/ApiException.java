package com.yanggochi.nurs.business;

/** 서비스 계층 예외. presentation에서 HTTP 응답으로 변환된다. */
public class ApiException extends RuntimeException {
    public final int status;
    public final transient Object detail;

    public ApiException(int status, String message, Object detail) {
        super(message);
        this.status = status;
        this.detail = detail;
    }

    public static ApiException badRequest(String m) { return new ApiException(400, m, null); }
    public static ApiException unauthorized(String m) { return new ApiException(401, m, null); }
    public static ApiException forbidden(String m) { return new ApiException(403, m, null); }
    /** SEC-03 원칙 2: 타 병동 리소스는 존재 여부를 숨기고 404 */
    public static ApiException notFound() { return new ApiException(404, "찾을 수 없습니다", null); }
    public static ApiException conflict(String m) { return new ApiException(409, m, null); }
    public static ApiException conflict(String m, Object detail) { return new ApiException(409, m, detail); }
}
