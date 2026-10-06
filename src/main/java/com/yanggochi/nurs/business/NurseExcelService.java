package com.yanggochi.nurs.business;

import com.yanggochi.nurs.domain.DutyRole;
import com.yanggochi.nurs.domain.NurseStatus;
import com.yanggochi.nurs.domain.Role;
import com.yanggochi.nurs.persistence.NurseRepository;
import jakarta.validation.Validator;
import org.apache.poi.ss.usermodel.*;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.*;
import java.util.function.Function;

/**
 * 간호사 명단 엑셀 내보내기 · 일괄 등록. 내보낸 파일을 그대로 다시 올릴 수 있도록 같은 양식을 쓴다.
 * 역할(role) 열은 참고용이며 가져오기에서는 무시한다 (권한은 AUTH-07로만).
 */
@Service
public class NurseExcelService {
    static final List<String> COLUMNS = List.of("이름", "역할", "듀티역할", "상태", "입사일", "경력(개월)", "숙련도", "소속시작일", "소속종료일");
    private static final Map<Role, String> ROLE = Map.of(Role.HEAD_NURSE, "수간호사", Role.NURSE, "간호사");
    private static final Map<DutyRole, String> DUTY_ROLE = Map.of(DutyRole.CHARGE, "차지", DutyRole.PRECEPTOR, "프리셉터",
            DutyRole.NEW, "신입", DutyRole.GENERAL, "일반");
    private static final Map<NurseStatus, String> STATUS = Map.of(NurseStatus.ACTIVE, "재직", NurseStatus.PREGNANT, "임신",
            NurseStatus.ON_LEAVE, "휴직", NurseStatus.RETIRED, "퇴사");

    /** 검증 메시지에 쓸 열 이름 */
    private static final Map<String, String> FIELD = Map.of("name", "이름", "dutyRole", "듀티역할", "status", "상태",
            "joinedAt", "입사일", "careerMonths", "경력(개월)", "skillLevel", "숙련도",
            "affiliationStart", "소속시작일", "affiliationEnd", "소속종료일");

    private final NurseService nurses;
    private final NurseRepository nurseRepo;
    private final MemberService members;
    private final AuditService audit;
    private final Validator validator;

    public NurseExcelService(NurseService nurses, NurseRepository nurseRepo, MemberService members, AuditService audit,
                             Validator validator) {
        this.nurses = nurses;
        this.nurseRepo = nurseRepo;
        this.members = members;
        this.audit = audit;
        this.validator = validator;
    }

    /** errors가 하나라도 있는 행은 반영할 수 없다. warnings는 확인용 */
    public record NurseImportRow(int rowNumber, NurseService.NurseForm form, List<String> errors, List<String> warnings) {
    }

    @Transactional
    public byte[] export(long userId, boolean includeRetired) {
        Member m = members.requireHead(userId);
        List<NurseService.NurseView> list = nurses.list(userId, includeRetired, null, null, null, "name", 0, 10_000).content();
        try (XSSFWorkbook wb = new XSSFWorkbook(); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            CellStyle date = wb.createCellStyle();
            date.setDataFormat(wb.createDataFormat().getFormat("yyyy-mm-dd"));
            Sheet sh = wb.createSheet("간호사 명단");
            Row h = sh.createRow(0);
            for (int i = 0; i < COLUMNS.size(); i++) h.createCell(i).setCellValue(COLUMNS.get(i));
            int r = 1;
            for (NurseService.NurseView n : list) {
                Row x = sh.createRow(r++);
                x.createCell(0).setCellValue(n.name());
                x.createCell(1).setCellValue(ROLE.get(n.role()));
                x.createCell(2).setCellValue(DUTY_ROLE.get(n.dutyRole()));
                x.createCell(3).setCellValue(STATUS.get(n.status()));
                setDate(x.createCell(4), n.joinedAt(), date);
                x.createCell(5).setCellValue(n.careerMonths());
                x.createCell(6).setCellValue(n.skillLevel());
                setDate(x.createCell(7), n.affiliationStart(), date);
                setDate(x.createCell(8), n.affiliationEnd(), date);
            }
            for (int i = 0; i < COLUMNS.size(); i++) sh.autoSizeColumn(i);
            wb.write(out);
            // 숙련도·경력·상태(임신) 등 민감 정보 반출이므로 감사 로그 기록
            audit.log(m.wardId(), userId, "NURSE_EXPORTED", "ward:" + m.wardId(), null, list.size() + " nurses");
            return out.toByteArray();
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    /** 1단계: 파싱·검증 결과만 돌려준다. 저장하지 않음 */
    public List<NurseImportRow> preview(long userId, InputStream file) {
        Member m = members.requireHead(userId);
        Set<String> existing = new HashSet<>();
        nurseRepo.findByWardId(m.wardId()).stream().filter(n -> n.status != NurseStatus.RETIRED).forEach(n -> existing.add(n.name));

        List<NurseImportRow> rows = new ArrayList<>();
        Set<String> inFile = new HashSet<>();
        DataFormatter fmt = new DataFormatter();
        try (Workbook wb = WorkbookFactory.create(file)) {
            Sheet sh = wb.getSheetAt(0);
            Row header = sh.getRow(sh.getFirstRowNum());
            if (header == null) throw ApiException.badRequest("빈 파일입니다");
            Map<String, Integer> col = new HashMap<>();
            for (Cell c : header) col.put(fmt.formatCellValue(c).trim(), c.getColumnIndex());
            List<String> missing = COLUMNS.stream().filter(c -> !c.equals("역할") && !c.equals("소속종료일") && !col.containsKey(c)).toList();
            if (!missing.isEmpty()) throw ApiException.badRequest("필수 열이 없습니다: " + missing);

            for (int i = sh.getFirstRowNum() + 1; i <= sh.getLastRowNum(); i++) {
                Row x = sh.getRow(i);
                if (x == null) continue;
                List<String> errors = new ArrayList<>();
                Function<String, Cell> cell = name -> col.containsKey(name) ? x.getCell(col.get(name)) : null;
                String name = fmt.formatCellValue(cell.apply("이름")).trim();
                if (name.isEmpty()) continue;

                NurseService.NurseForm form = new NurseService.NurseForm(name,
                        label(fmt.formatCellValue(cell.apply("듀티역할")), DUTY_ROLE, DutyRole.class, "듀티역할", errors),
                        label(fmt.formatCellValue(cell.apply("상태")), STATUS, NurseStatus.class, "상태", errors),
                        date(cell.apply("입사일"), fmt, "입사일", errors),
                        number(fmt.formatCellValue(cell.apply("경력(개월)")), "경력(개월)", errors),
                        number(fmt.formatCellValue(cell.apply("숙련도")), "숙련도", errors),
                        date(cell.apply("소속시작일"), fmt, "소속시작일", errors),
                        date(cell.apply("소속종료일"), fmt, "소속종료일", errors),
                        Set.of());
                // 파싱 단계에서 이미 오류가 난 열은 '필수 값' 오류를 중복으로 붙이지 않는다
                for (String c : check(form))
                    if (errors.stream().noneMatch(prev -> prev.startsWith(c.substring(0, c.indexOf(':') + 1)))) errors.add(c);
                List<String> warnings = new ArrayList<>();
                if (existing.contains(name)) warnings.add("이미 등록된 이름입니다");
                if (!inFile.add(name)) warnings.add("파일 안에 같은 이름이 있습니다");
                rows.add(new NurseImportRow(i + 1, form, errors, warnings));
            }
        } catch (IOException | RuntimeException e) {
            if (e instanceof ApiException a) throw a;
            throw ApiException.badRequest("엑셀 형식을 읽을 수 없습니다");
        }
        return rows;
    }

    /** 2단계: 미리보기에서 확인한 행을 한 트랜잭션으로 등록. 한 행이라도 틀리면 전체 취소 */
    @Transactional
    public List<NurseService.NurseView> apply(long userId, List<NurseService.NurseForm> forms) {
        Member m = members.requireHead(userId);
        if (forms.isEmpty()) throw ApiException.badRequest("등록할 행이 없습니다");
        Map<Integer, List<String>> errors = new TreeMap<>();
        for (int i = 0; i < forms.size(); i++) {
            List<String> e = check(forms.get(i));
            if (!e.isEmpty()) errors.put(i + 1, e);
        }
        if (!errors.isEmpty()) throw new ApiException(400, "잘못된 항목이 있습니다 (번호는 1부터)", errors);
        List<NurseService.NurseView> out = forms.stream().map(f -> nurses.register(userId, f).nurse()).toList();
        audit.log(m.wardId(), userId, "NURSE_IMPORTED", "ward:" + m.wardId(), null, out.size() + " nurses");
        return out;
    }

    private List<String> check(NurseService.NurseForm f) {
        List<String> e = new ArrayList<>();
        validator.validate(f).forEach(v -> {
            var a = v.getConstraintDescriptor().getAnnotation();
            boolean required = a instanceof jakarta.validation.constraints.NotNull || a instanceof jakarta.validation.constraints.NotBlank;
            String path = v.getPropertyPath().toString();
            e.add(FIELD.getOrDefault(path, path) + ": " + (required ? "필수 값입니다" : v.getMessage()));
        });
        if (f.status() == NurseStatus.RETIRED) e.add("상태: 퇴사자는 등록할 수 없습니다");
        if (f.affiliationStart() != null && f.affiliationEnd() != null && f.affiliationEnd().isBefore(f.affiliationStart()))
            e.add("소속종료일: 소속 시작일보다 빠릅니다");
        return e;
    }

    /** 한글 표기("차지") 또는 코드("CHARGE") 둘 다 허용 */
    private static <E extends Enum<E>> E label(String raw, Map<E, String> labels, Class<E> type, String col, List<String> errors) {
        String t = raw.trim();
        if (t.isEmpty()) return null; // 필수 여부는 Validator가 판단
        for (var e : labels.entrySet()) if (e.getValue().equals(t)) return e.getKey();
        try {
            return Enum.valueOf(type, t.toUpperCase());
        } catch (IllegalArgumentException ex) {
            errors.add(col + ": 알 수 없는 값 '" + t + "' (" + String.join("/", labels.values()) + ")");
            return null;
        }
    }

    private static LocalDate date(Cell c, DataFormatter fmt, String col, List<String> errors) {
        if (c == null) return null;
        if (c.getCellType() == CellType.NUMERIC && DateUtil.isCellDateFormatted(c)) return c.getLocalDateTimeCellValue().toLocalDate();
        String t = fmt.formatCellValue(c).trim().replace('.', '-').replace('/', '-');
        if (t.isEmpty()) return null;
        try {
            return LocalDate.parse(t);
        } catch (DateTimeParseException e) {
            errors.add(col + ": 날짜 형식은 yyyy-MM-dd 입니다 ('" + t + "')");
            return null;
        }
    }

    private static int number(String raw, String col, List<String> errors) {
        String t = raw.trim();
        if (t.isEmpty()) return 0;
        try {
            return (int) Double.parseDouble(t);
        } catch (NumberFormatException e) {
            errors.add(col + ": 숫자가 아닙니다 ('" + t + "')");
            return 0;
        }
    }

    private static void setDate(Cell c, LocalDate d, CellStyle style) {
        if (d == null) return;
        c.setCellValue(d);
        c.setCellStyle(style);
    }
}
