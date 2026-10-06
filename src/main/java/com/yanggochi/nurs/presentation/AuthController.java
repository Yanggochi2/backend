package com.yanggochi.nurs.presentation;

import com.yanggochi.nurs.business.AuthService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpSession;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import static com.yanggochi.nurs.presentation.WebConfig.USER_ID;

@RestController
public class AuthController {
    private final AuthService auth;

    public AuthController(AuthService auth) {
        this.auth = auth;
    }

    @PostMapping("/api/auth/signup")
    @ResponseStatus(HttpStatus.CREATED)
    public AuthService.Me signup(@Valid @RequestBody AuthService.Signup body, HttpServletRequest req) {
        return startSession(auth.signup(body), req);
    }

    @PostMapping("/api/auth/login")
    public AuthService.Me login(@Valid @RequestBody AuthService.Login body, HttpServletRequest req) {
        return startSession(auth.login(body), req);
    }

    @PostMapping("/api/auth/logout")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void logout(@SessionAttribute(USER_ID) Long userId, HttpSession session) {
        auth.logout(userId);
        session.invalidate();
    }

    @GetMapping("/api/me")
    public AuthService.Me me(@SessionAttribute(USER_ID) Long userId) {
        return auth.me(userId);
    }

    private AuthService.Me startSession(long userId, HttpServletRequest req) {
        req.getSession(true);
        req.changeSessionId(); // 세션 고정 공격 방지
        req.getSession().setAttribute(USER_ID, userId);
        return auth.me(userId);
    }
}
