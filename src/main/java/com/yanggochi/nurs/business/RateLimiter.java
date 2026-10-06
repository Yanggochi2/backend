package com.yanggochi.nurs.business;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 키별 슬라이딩 윈도우 시도 횟수 제한. 초과하면 429.
 * ponytail: 인메모리라 서버 다중화·재시작 시 공유되지 않는다. 공유 저장소로 옮기는 건 Yanggochi2/backend#23
 */
class RateLimiter {
    private final int max;
    private final Duration window;
    private final Map<Object, Deque<Instant>> hits = new ConcurrentHashMap<>();

    RateLimiter(int max, Duration window) {
        this.max = max;
        this.window = window;
    }

    /** 한도를 넘었으면 429 */
    void check(Object key) {
        Deque<Instant> q = hits.get(key);
        if (q == null) return;
        synchronized (q) {
            Instant cutoff = Instant.now().minus(window);
            while (!q.isEmpty() && q.peekFirst().isBefore(cutoff)) q.pollFirst();
            if (q.size() >= max) throw new ApiException(429, "시도 횟수를 초과했습니다. 잠시 후 다시 시도하세요", null);
        }
    }

    void record(Object key) {
        Deque<Instant> q = hits.computeIfAbsent(key, k -> new ArrayDeque<>());
        synchronized (q) {
            q.addLast(Instant.now());
        }
    }

    void reset(Object key) {
        hits.remove(key);
    }
}
