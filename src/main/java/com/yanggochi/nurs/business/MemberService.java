package com.yanggochi.nurs.business;

import com.yanggochi.nurs.domain.NurseStatus;
import com.yanggochi.nurs.persistence.Nurse;
import com.yanggochi.nurs.persistence.NurseRepository;
import org.springframework.stereotype.Service;

import java.util.Optional;

@Service
public class MemberService {
    private final NurseRepository nurses;

    public MemberService(NurseRepository nurses) {
        this.nurses = nurses;
    }

    public Optional<Nurse> membership(long userId) {
        // ponytail: 다중 병동 소속 미허용(COM-01 🔶) 가정. 허용 시 요청에 병동 선택 추가
        return nurses.findFirstByUserIdAndStatusNot(userId, NurseStatus.RETIRED);
    }

    public Member require(long userId) {
        Nurse n = membership(userId).orElseThrow(() -> ApiException.forbidden("병동 소속이 필요합니다"));
        return new Member(userId, n.wardId, n.id, n.role);
    }

    public Member requireHead(long userId) {
        Member m = require(userId);
        if (!m.isHead()) throw ApiException.forbidden("수간호사 권한이 필요합니다");
        return m;
    }
}
