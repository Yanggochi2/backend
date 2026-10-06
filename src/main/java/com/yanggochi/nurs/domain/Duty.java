package com.yanggochi.nurs.domain;

/** COM-03. null 셀은 미배정이며 O와 구분한다. */
public enum Duty {
    D, E, N, O, AL, ED;

    public boolean isWork() {
        return this == D || this == E || this == N;
    }
}
