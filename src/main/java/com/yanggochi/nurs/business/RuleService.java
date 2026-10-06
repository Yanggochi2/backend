package com.yanggochi.nurs.business;

import com.yanggochi.nurs.domain.Rules;
import com.yanggochi.nurs.persistence.Holiday;
import com.yanggochi.nurs.persistence.HolidayRepository;
import com.yanggochi.nurs.persistence.Ward;
import com.yanggochi.nurs.persistence.WardRepository;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.YearMonth;
import java.util.List;

/** RULE-01·02·03·05 */
@Service
public class RuleService {
    private final WardRepository wards;
    private final HolidayRepository holidays;
    private final MemberService members;
    private final AuditService audit;

    public RuleService(WardRepository wards, HolidayRepository holidays, MemberService members, AuditService audit) {
        this.wards = wards;
        this.holidays = holidays;
        this.members = members;
        this.audit = audit;
    }

    public record RulesForm(@Min(0) int requiredD, @Min(0) int requiredE, @Min(0) int requiredN,
                            @Min(1) int maxConsecutiveNights, @Min(1) int maxConsecutiveWorkDays,
                            boolean forbidNightToDay) {
        Rules toRules() {
            return new Rules(requiredD, requiredE, requiredN, maxConsecutiveNights, maxConsecutiveWorkDays, forbidNightToDay);
        }
    }

    public record HolidayForm(@NotNull LocalDate date, @NotBlank String name) {
    }

    public record HolidayView(long id, LocalDate date, String name) {
    }

    public Rules get(long userId) {
        return ward(members.require(userId)).rules();
    }

    @Transactional
    public Rules update(long userId, RulesForm f) {
        Member m = members.requireHead(userId);
        Ward w = ward(m);
        Rules before = w.rules();
        w.apply(f.toRules());
        audit.log(m.wardId(), userId, "RULES_CHANGED", "ward:" + w.id, before, w.rules());
        return w.rules();
    }

    @Transactional
    public Rules applyPreset(long userId, Rules.Preset preset) {
        Member m = members.requireHead(userId);
        Ward w = ward(m);
        Rules before = w.rules();
        w.apply(Rules.preset(preset, w.requiredD, w.requiredE, w.requiredN));
        audit.log(m.wardId(), userId, "RULES_CHANGED", "ward:" + w.id, before, w.rules());
        return w.rules();
    }

    public List<HolidayView> holidays(long userId, YearMonth ym) {
        return inMonth(members.require(userId).wardId(), ym).stream()
                .map(h -> new HolidayView(h.id, h.date, h.name)).toList();
    }

    List<Holiday> inMonth(long wardId, YearMonth ym) {
        return holidays.findByWardIdAndDateBetweenOrderByDate(wardId, ym.atDay(1), ym.atEndOfMonth());
    }

    @Transactional
    public HolidayView addHoliday(long userId, HolidayForm f) {
        Member m = members.requireHead(userId);
        if (!holidays.findByWardIdAndDateBetweenOrderByDate(m.wardId(), f.date(), f.date()).isEmpty())
            throw ApiException.conflict("이미 등록된 공휴일입니다");
        Holiday h = new Holiday();
        h.wardId = m.wardId();
        h.date = f.date();
        h.name = f.name();
        holidays.save(h);
        audit.log(m.wardId(), userId, "RULES_CHANGED", "holiday:" + h.date, null, h.name);
        return new HolidayView(h.id, h.date, h.name);
    }

    @Transactional
    public void deleteHoliday(long userId, long id) {
        Member m = members.requireHead(userId);
        Holiday h = holidays.findByIdAndWardId(id, m.wardId()).orElseThrow(ApiException::notFound);
        holidays.delete(h);
        audit.log(m.wardId(), userId, "RULES_CHANGED", "holiday:" + h.date, h.name, null);
    }

    private Ward ward(Member m) {
        return wards.findById(m.wardId()).orElseThrow(ApiException::notFound);
    }
}
