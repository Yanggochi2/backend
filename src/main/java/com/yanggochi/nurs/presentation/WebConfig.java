package com.yanggochi.nurs.presentation;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.servlet.http.HttpSession;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.servlet.HandlerInterceptor;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * AUTH-05. 서버 세션 + HttpOnly/Secure/SameSite 쿠키 (application.properties).
 * 토큰을 body·쿼리로 주고받지 않는다. 로그인하지 않은 요청은 401.
 */
@Configuration
public class WebConfig implements WebMvcConfigurer {
    static final String USER_ID = "userId";

    @Bean
    PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(new HandlerInterceptor() {
            @Override
            public boolean preHandle(HttpServletRequest req, HttpServletResponse res, Object handler) {
                HttpSession s = req.getSession(false);
                if (s != null && s.getAttribute(USER_ID) != null) return true;
                res.setStatus(401);
                return false;
            }
        }).addPathPatterns("/api/**").excludePathPatterns("/api/auth/signup", "/api/auth/login");
    }
}
