package com.yanggochi.nurs.business;

import com.yanggochi.nurs.domain.Role;

/** 세션 사용자의 병동 소속. 병동·역할은 항상 서버가 판정한다 (SEC-03 원칙 1·3). */
public record Member(long userId, long wardId, long nurseId, Role role) {
    public boolean isHead() {
        return role == Role.HEAD_NURSE;
    }
}
