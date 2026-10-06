package com.yanggochi.nurs.domain;

/** RULE-01·02 값 + AUTH-00 듀티별 필요 인원 */
public record Rules(int requiredD, int requiredE, int requiredN,
                    int maxConsecutiveNights, int maxConsecutiveWorkDays, boolean forbidNightToDay) {

    /** RULE-05 프리셋 */
    public enum Preset { STANDARD, MINIMAL }

    public static Rules preset(Preset p, int d, int e, int n) {
        return p == Preset.STANDARD
                ? new Rules(d, e, n, 3, 5, true)
                : new Rules(d, e, n, 31, 31, false);
    }

    public int required(Duty duty) {
        return switch (duty) {
            case D -> requiredD;
            case E -> requiredE;
            case N -> requiredN;
            default -> 0;
        };
    }
}
