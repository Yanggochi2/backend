package com.yanggochi.nurs.persistence;

import com.yanggochi.nurs.domain.Rules;
import jakarta.persistence.*;

@Entity
public class Ward {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    public Long id;
    @Column(nullable = false)
    public String name;
    @Column(nullable = false)
    public String hospital;
    /** AUTH-04. 재발급 시 덮어써서 기존 코드는 즉시 무효 */
    @Column(unique = true)
    public String code;
    public int requiredD, requiredE, requiredN;
    public int maxConsecutiveNights, maxConsecutiveWorkDays;
    public boolean forbidNightToDay;

    public Rules rules() {
        return new Rules(requiredD, requiredE, requiredN, maxConsecutiveNights, maxConsecutiveWorkDays, forbidNightToDay);
    }

    public void apply(Rules r) {
        requiredD = r.requiredD();
        requiredE = r.requiredE();
        requiredN = r.requiredN();
        maxConsecutiveNights = r.maxConsecutiveNights();
        maxConsecutiveWorkDays = r.maxConsecutiveWorkDays();
        forbidNightToDay = r.forbidNightToDay();
    }
}
