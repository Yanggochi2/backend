package com.yanggochi.nurs.business;

import com.yanggochi.nurs.domain.Duty;
import com.yanggochi.nurs.domain.Violation;
import org.apache.poi.ss.usermodel.*;
import org.apache.poi.xssf.usermodel.XSSFCellStyle;
import org.apache.poi.xssf.usermodel.XSSFColor;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.*;

/** SCH-09 엑셀 내보내기 · SCH-11 엑셀 가져오기 */
@Service
public class ExcelService {
    /** 듀티 색상 (RGB) */
    private static final Map<Duty, byte[]> COLORS = Map.of(
            Duty.D, rgb(0xFFF2CC), Duty.E, rgb(0xE2EFDA), Duty.N, rgb(0xDDEBF7),
            Duty.O, rgb(0xEDEDED), Duty.AL, rgb(0xFCE4D6), Duty.ED, rgb(0xE4DFEC));
    /** 엑셀 코드 → 듀티 제안값. 나머지는 사용자가 매핑 */
    private static final Map<String, Duty> ALIASES = Map.of("OFF", Duty.O, "/", Duty.O, "휴", Duty.O,
            "DAY", Duty.D, "EVE", Duty.E, "NIGHT", Duty.N);

    private final ScheduleService schedules;
    private final MemberService members;
    private final AuditService audit;

    public ExcelService(ScheduleService schedules, MemberService members, AuditService audit) {
        this.schedules = schedules;
        this.members = members;
        this.audit = audit;
    }

    /**
     * nurseId: 이름이 한 명과만 일치할 때. 동명이인이면 null이고 candidates 중에서 사용자가 고른다.
     * warning: 동명이인 / 미등록 이름 / 같은 간호사가 여러 행
     */
    public record ImportRow(int rowNumber, String name, Long nurseId, List<Long> candidates, String warning) {
    }

    /** nurseId가 null인 셀은 같은 rowNumber 행의 간호사를 사용자가 지정한 뒤 apply로 보낸다 */
    public record ImportCell(int rowNumber, Long nurseId, LocalDate date, String raw) {
    }

    /** codes: 파일에 나온 코드별 제안 듀티(null = 인식 불가, 사용자가 지정해야 함) */
    public record ImportPreview(List<ImportRow> rows, Map<String, Duty> codes, List<ImportCell> cells) {
    }

    @Transactional
    public byte[] export(long userId, String ym) {
        Member m = members.requireHead(userId);
        ScheduleService.ScheduleView v = schedules.get(userId, ym);
        List<LocalDate> days = YearMonth.parse(v.yearMonth()).atDay(1)
                .datesUntil(YearMonth.parse(v.yearMonth()).atEndOfMonth().plusDays(1)).toList();
        Map<Long, ScheduleService.Stat> stats = new HashMap<>();
        v.stats().forEach(s -> stats.put(s.nurseId(), s));

        try (XSSFWorkbook wb = new XSSFWorkbook(); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            Map<Duty, CellStyle> styles = new EnumMap<>(Duty.class);
            COLORS.forEach((duty, color) -> {
                XSSFCellStyle st = wb.createCellStyle();
                st.setFillForegroundColor(new XSSFColor(color, null));
                st.setFillPattern(FillPatternType.SOLID_FOREGROUND);
                st.setAlignment(HorizontalAlignment.CENTER);
                styles.put(duty, st);
            });
            Sheet sh = wb.createSheet(v.yearMonth());
            Row h = sh.createRow(0);
            h.createCell(0).setCellValue("이름");
            for (int i = 0; i < days.size(); i++) h.createCell(1 + i).setCellValue(days.get(i).getDayOfMonth());
            String[] statCols = {"D", "E", "N", "OFF", "AL", "OFF목표", "주말", "공휴일"};
            for (int i = 0; i < statCols.length; i++) h.createCell(1 + days.size() + i).setCellValue(statCols[i]);

            int r = 1;
            for (ScheduleService.Row row : v.rows()) {
                Row x = sh.createRow(r++);
                x.createCell(0).setCellValue(row.name());
                for (int i = 0; i < days.size(); i++) {
                    Duty d = row.cells().get(days.get(i));
                    if (d == null) continue;
                    Cell c = x.createCell(1 + i);
                    c.setCellValue(d.name());
                    c.setCellStyle(styles.get(d));
                }
                ScheduleService.Stat s = stats.get(row.nurseId());
                int[] vals = {s.d(), s.e(), s.n(), s.off(), s.al(), s.offTarget(), s.weekend(), s.holiday()};
                for (int i = 0; i < vals.length; i++) x.createCell(1 + days.size() + i).setCellValue(vals[i]);
            }
            for (Duty duty : List.of(Duty.D, Duty.E, Duty.N)) {
                Row x = sh.createRow(r++);
                x.createCell(0).setCellValue(duty + " 인원 (필요 " + v.rules().required(duty) + ")");
                for (int i = 0; i < days.size(); i++)
                    x.createCell(1 + i).setCellValue(v.coverage().get(days.get(i)).getOrDefault(duty, 0));
            }
            sh.autoSizeColumn(0);
            wb.write(out);
            // 내보내기는 정보 유출 경로이므로 감사 로그 필수 (SCH-09)
            audit.log(m.wardId(), userId, "EXCEL_EXPORTED", "schedule:" + v.yearMonth(), null, null);
            return out.toByteArray();
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    /** SCH-11 1단계: 행=간호사(첫 열 이름), 열=날짜(첫 행 일자) 구조를 파싱해 매칭 결과만 돌려준다. 저장하지 않음 */
    public ImportPreview preview(long userId, String ym, InputStream file) {
        members.requireHead(userId);
        ScheduleService.ScheduleView v = schedules.get(userId, ym);
        YearMonth month = YearMonth.parse(v.yearMonth());
        // 리뷰 #6: 동명이인이 있으면 한 사람에게 몰아 넣지 않도록 이름별 후보를 모두 모은다
        Map<String, List<Long>> byName = new HashMap<>();
        v.rows().forEach(row -> byName.computeIfAbsent(row.name().trim(), k -> new ArrayList<>()).add(row.nurseId()));
        Set<Long> seen = new HashSet<>();

        List<ImportRow> rows = new ArrayList<>();
        Map<String, Duty> codes = new TreeMap<>();
        List<ImportCell> cells = new ArrayList<>();
        DataFormatter fmt = new DataFormatter();
        try (Workbook wb = WorkbookFactory.create(file)) {
            Sheet sh = wb.getSheetAt(0);
            Row header = sh.getRow(sh.getFirstRowNum());
            if (header == null) throw ApiException.badRequest("빈 파일입니다");
            Map<Integer, LocalDate> dayCols = new HashMap<>();
            for (Cell c : header) {
                String t = fmt.formatCellValue(c).trim().replaceAll("일$", "");
                if (t.matches("\\d{1,2}") && Integer.parseInt(t) >= 1 && Integer.parseInt(t) <= month.lengthOfMonth())
                    dayCols.put(c.getColumnIndex(), month.atDay(Integer.parseInt(t)));
            }
            if (dayCols.isEmpty()) throw ApiException.badRequest("첫 행에서 날짜 열을 찾지 못했습니다");
            for (int i = sh.getFirstRowNum() + 1; i <= sh.getLastRowNum(); i++) {
                Row x = sh.getRow(i);
                if (x == null) continue;
                String name = fmt.formatCellValue(x.getCell(0)).trim();
                if (name.isEmpty()) continue;
                List<Long> candidates = byName.getOrDefault(name, List.of());
                Long nurseId = candidates.size() == 1 ? candidates.get(0) : null;
                String warning = candidates.isEmpty() ? "등록되지 않은 이름입니다"
                        : candidates.size() > 1 ? "동명이인 " + candidates.size() + "명 — 간호사를 지정하세요"
                        : !seen.add(nurseId) ? "같은 간호사가 여러 행에 있습니다" : null;
                rows.add(new ImportRow(i + 1, name, nurseId, candidates, warning));
                if (candidates.isEmpty()) continue;
                for (var e : dayCols.entrySet()) {
                    String raw = fmt.formatCellValue(x.getCell(e.getKey())).trim().toUpperCase();
                    if (raw.isEmpty()) continue;
                    codes.computeIfAbsent(raw, ExcelService::suggest);
                    cells.add(new ImportCell(i + 1, nurseId, e.getValue(), raw));
                }
            }
        } catch (IOException | RuntimeException e) {
            if (e instanceof ApiException a) throw a;
            throw ApiException.badRequest("엑셀 형식을 읽을 수 없습니다");
        }
        return new ImportPreview(rows, codes, cells);
    }

    /** SCH-11 2단계: 사용자가 확인한 매핑으로 셀 편집 + 위반 검증 */
    @Transactional
    public List<Violation> apply(long userId, String ym, List<ScheduleService.CellEdit> cells) {
        Member m = members.requireHead(userId);
        List<Violation> v = schedules.editCells(userId, ym, cells);
        audit.log(m.wardId(), userId, "EXCEL_IMPORTED", "schedule:" + ym, null, cells.size() + " cells");
        return v;
    }

    private static Duty suggest(String raw) {
        if (ALIASES.containsKey(raw)) return ALIASES.get(raw);
        try {
            Duty d = Duty.valueOf(raw);
            return d == Duty.AL ? null : d; // AL은 연차 승인으로만 (COM-03)
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    private static byte[] rgb(int c) {
        return new byte[]{(byte) (c >> 16), (byte) (c >> 8), (byte) c};
    }
}
