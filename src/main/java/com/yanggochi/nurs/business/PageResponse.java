package com.yanggochi.nurs.business;

import org.springframework.data.domain.Page;

import java.util.List;

public record PageResponse<T>(List<T> content, int page, int size, long total) {
    public static <T> PageResponse<T> of(Page<T> p) {
        return new PageResponse<>(p.getContent(), p.getNumber(), p.getSize(), p.getTotalElements());
    }
}
