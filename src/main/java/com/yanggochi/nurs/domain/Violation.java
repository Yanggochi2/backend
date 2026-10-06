package com.yanggochi.nurs.domain;

/** SCH-06 위반 단위. nurseId가 null이면 병동 단위(커버리지) 위반. */
public record Violation(Severity severity, String ruleId, Long nurseId, java.time.LocalDate date, Duty current, String message) {
}
