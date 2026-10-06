#!/bin/bash
# 전체 API E2E 점검 (빈 DB 기준). 서버를 리마인드 10초 주기로 띄운 뒤 실행한다.
#
#   ./gradlew bootJar
#   java -jar build/libs/backend-0.0.1-SNAPSHOT.jar --spring.profiles.active=local --server.port=18080 \
#        "--reminder.cron=*/10 * * * * *" &
#   scripts/e2e.sh 18080
#
# 필요: curl, python3. 날짜는 실행일 기준으로 계산한다. [리뷰#N] 항목은 PR #14 리뷰 지적의 회귀 검사.
FIXTURE="$(cd "$(dirname "$0")" && pwd)/fixtures/roster.xlsx"
cd "$(mktemp -d)"
U=localhost:${1:-18080}/api
DATES=$(mktemp)
cat > "$DATES" <<'EOF'
from datetime import datetime, timedelta, timezone
import json
today = datetime.now(timezone(timedelta(hours=9))).date()
t = today + timedelta(days=1)
nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
ym, tm = nxt.strftime("%Y-%m"), t.strftime("%Y-%m")   # 주 시나리오는 다음 달(모두 미래 날짜), 리마인드는 '내일'의 달
print(f"YM={ym}; TM={tm}; D_T={t}")
for i, d in enumerate(range(20, 26)): print(f"D_L{i}={ym}-{d:02d}")
def js(cells, duty):
    out = [dict(nurseId="NID", date=str(d), **({"duty": x} if duty else {})) for d, x in cells]
    return json.dumps(out)[1:-1].replace('"NID"', "NID")
main = [(nxt.replace(day=9), "O"), (nxt.replace(day=10), "D"), (nxt.replace(day=11), "O")]
rem = [(d, x) for d, x in [(t - timedelta(days=1), "O"), (t, "D"), (t + timedelta(days=1), "O")] if d.strftime("%Y-%m") == tm]
if tm == ym: main += rem   # 오늘이 말일이면 같은 근무표에서 함께 고정
print(f"FIXCELLS='{js(main, True)}'"); print(f"FIXREFS='{js(main, False)}'")
print(f"TFIXCELLS='{js(rem, True)}'"); print(f"TFIXREFS='{js(rem, False)}'")
EOF
eval "$(python3 "$DATES")"
PASS=0; FAIL=0
rm -f h.jar n.jar o.jar out.json

# req <jar> <method> <path> [json-body]  → 응답 본문은 out.json, 상태코드는 $CODE
req() {
  local jar=$1 m=$2 p=$3 body=$4
  if [ -n "$body" ]; then
    CODE=$(curl -s -o out.json -w '%{http_code}' -b $jar -c $jar -X $m -H 'Content-Type: application/json' "$U$p" -d "$body")
  else
    CODE=$(curl -s -o out.json -w '%{http_code}' -b $jar -c $jar -X $m "$U$p")
  fi
}
# ok <기대코드> <설명> [파이썬 조건식, d=응답 JSON]
ok() {
  local want=$1 desc=$2 cond=$3 good=1
  [ "$CODE" = "$want" ] || good=0
  if [ $good = 1 ] && [ -n "$cond" ]; then
    python3 -c "import json,sys;d=json.load(open('out.json'));sys.exit(0 if ($cond) else 1)" 2>/dev/null || good=0
  fi
  if [ $good = 1 ]; then PASS=$((PASS+1)); echo "  ✅ $desc"; else FAIL=$((FAIL+1)); echo "  ❌ $desc  (HTTP $CODE: $(head -c 200 out.json))"; fi
}
val() { python3 -c "import json;d=json.load(open('out.json'));print($1)"; }
NURSE='{"name":"%s","dutyRole":"%s","status":"%s","joinedAt":"2025-01-01","careerMonths":%d,"skillLevel":3,"affiliationStart":"2025-01-01"}'

echo "■ 인증 (AUTH-01·05)"
req h.jar POST /auth/signup '{"name":"김수간","email":"head@x.com","password":"pass1234","agreeTerms":true,"role":"HEAD_NURSE","wardId":1}'
ok 201 "회원가입 (role·wardId 보내도 무시)" 'd["role"] is None and d["wardId"] is None'
req o.jar POST /auth/signup '{"name":"a","email":"weak@x.com","password":"short","agreeTerms":true}'; ok 400 "약한 비밀번호 거부"
req o.jar POST /auth/signup '{"name":"a","email":"t@x.com","password":"pass1234","agreeTerms":false}'; ok 400 "약관 미동의 거부"
req o.jar POST /auth/signup '{"name":"a","email":"HEAD@x.com","password":"pass1234","agreeTerms":true}'; ok 409 "이메일 중복 거부 (대소문자 무관)"
rm -f o.jar; req o.jar GET /me; ok 401 "비로그인 요청 401"
req o.jar POST /auth/login '{"email":"head@x.com","password":"wrong1234"}'; ok 401 "틀린 비밀번호 401"
COOKIE=$(curl -si $U/auth/login -H 'Content-Type: application/json' -d '{"email":"head@x.com","password":"pass1234"}' | grep -i set-cookie)
[[ "$COOKIE" == *HttpOnly* && "$COOKIE" == *SameSite=Strict* ]] && { PASS=$((PASS+1)); echo "  ✅ 세션 쿠키 HttpOnly·SameSite"; } || { FAIL=$((FAIL+1)); echo "  ❌ 쿠키 속성: $COOKIE"; }

echo "■ 병동·가입 (AUTH-00·03·04·06·07)"
req h.jar POST /wards '{"name":"7병동","hospital":"한빛","requiredD":2,"requiredE":2,"requiredN":2,"preset":"STANDARD"}'
ok 201 "병동 개설 + 코드 발급" 'len(d["code"])==8'; CODE1=$(val 'd["code"]')
req h.jar GET /me; ok 200 "개설자 = 수간호사" 'd["role"]=="HEAD_NURSE"'
req h.jar POST /wards '{"name":"x","hospital":"x","requiredD":1,"requiredE":1,"requiredN":1,"preset":"MINIMAL"}'; ok 409 "이미 소속이면 개설 불가"
req h.jar POST /ward/code; ok 200 "코드 재발급"; CODE2=$(val 'd["code"]')
req n.jar POST /auth/signup '{"name":"이간호","email":"nurse@x.com","password":"pass1234","agreeTerms":true}'
req n.jar POST /ward/join "{\"code\":\"$CODE1\"}"; ok 400 "폐기된 옛 코드로 가입 불가"
req n.jar POST /ward/join "{\"code\":\"$(echo $CODE2 | tr A-Z a-z)\"}"; ok 202 "새 코드로 가입 신청 (소문자 입력 허용)"
req n.jar POST /ward/join "{\"code\":\"$CODE2\"}"; ok 409 "중복 신청 거부"
req n.jar GET /nurses; ok 403 "승인 전에는 병동 기능 사용 불가"
req n.jar GET /me; ok 200 "승인 대기 표시" 'd["joinPending"]==True'
req h.jar GET /ward/join-requests; ok 200 "대기 목록" 'len(d)==1'; JID=$(val 'd[0]["id"]')
req h.jar POST /ward/join-requests/$JID/approve; ok 200 "가입 승인"
req n.jar GET /me; ok 200 "승인 후 NURSE로 소속" 'd["role"]=="NURSE"'; NID=$(val 'd["nurseId"]')
req h.jar GET /me; HID=$(val 'd["nurseId"]')
req h.jar PUT /nurses/$NID '{"name":"이간호","dutyRole":"GENERAL","status":"ACTIVE","joinedAt":"2025-01-01","careerMonths":24,"skillLevel":3,"affiliationStart":"2025-01-01"}'
ok 200 "간호사 정보 수정 (+위반 목록 응답)" 'd["nurse"]["affiliationStart"]=="2025-01-01" and d["violations"]==[]'
FIXCELLS=${FIXCELLS//NID/$NID}; FIXREFS=${FIXREFS//NID/$NID}; TFIXCELLS=${TFIXCELLS//NID/$NID}; TFIXREFS=${TFIXREFS//NID/$NID}
req n.jar GET /ward; ok 200 "일반 간호사에게 병동 코드 숨김" 'd["code"] is None'
req n.jar POST /ward/code; ok 403 "일반 간호사 코드 재발급 불가"
for i in 1 2 3 4 5; do req o.jar POST /auth/signup "{\"name\":\"r$i\",\"email\":\"r$i@x.com\",\"password\":\"pass1234\",\"agreeTerms\":true}" >/dev/null; done
rm -f o.jar; req o.jar POST /auth/signup '{"name":"brute","email":"brute@x.com","password":"pass1234","agreeTerms":true}'
for i in $(seq 1 10); do req o.jar POST /ward/join '{"code":"WRONG000"}'; done
req o.jar POST /ward/join "{\"code\":\"$CODE2\"}"; ok 429 "코드 시도 시간당 10회 제한"

echo "■ 간호사 관리 (NUR)"
req h.jar POST /nurses "$(printf "$NURSE" 차지1 CHARGE ACTIVE 120)"; ok 201 "간호사 등록" 'd["warnings"]==[]'; CH=$(val 'd["nurse"]["id"]')
req h.jar POST /nurses "$(printf "$NURSE" 신입1 NEW ACTIVE 2)"; NEWID=$(val 'd["nurse"]["id"]')
req h.jar POST /nurses "$(printf "$NURSE" 임신1 GENERAL PREGNANT 60)"; PREG=$(val 'd["nurse"]["id"]')
for i in $(seq 1 9); do req h.jar POST /nurses "$(printf "$NURSE" 일반$i GENERAL ACTIVE $((i*12)))"; done; G9=$(val 'd["nurse"]["id"]')
req h.jar POST /nurses '{"name":"x","dutyRole":"GENERAL","status":"RETIRED","joinedAt":"2025-01-01","careerMonths":1,"skillLevel":3,"affiliationStart":"2025-01-01"}'; ok 400 "등록 시 RETIRED 불가"
req h.jar POST /nurses '{"name":"x","dutyRole":"GENERAL","status":"ACTIVE","joinedAt":"2025-01-01","careerMonths":1,"skillLevel":9,"affiliationStart":"2025-01-01"}'; ok 400 "숙련도 1~5 검증"
req h.jar GET "/nurses?sort=career&size=3"; ok 200 "수간호사: 경력순 + 전 필드" 'd["content"][0]["careerMonths"]==120 and d["total"]==14'
req n.jar GET "/nurses?size=100"; ok 200 "일반 간호사: 숙련도·경력·상태 제외" 'all(x["skillLevel"] is None and x["careerMonths"] is None and x["status"] is None for x in d["content"])'
req n.jar GET "/nurses?status=PREGNANT"; ok 200 "일반 간호사는 상태 필터로 임신 여부 못 알아냄" 'd["total"]==0'
req h.jar GET "/nurses?dutyRole=NEW"; ok 200 "듀티역할 필터" 'd["total"]==1'
req n.jar POST /nurses "$(printf "$NURSE" x GENERAL ACTIVE 1)"; ok 403 "일반 간호사 등록 불가"
req h.jar POST /nurses/$HID/retire '{"affiliationEnd":"2099-12-31"}'; ok 409 "마지막 수간호사 퇴사 차단"

echo "■ 규칙·공휴일 (RULE)"
req h.jar GET /rules; ok 200 "규칙 조회" 'd["maxConsecutiveNights"]==3'
req h.jar POST /rules/preset '{"preset":"MINIMAL"}'; ok 200 "최소 규칙 프리셋" 'd["forbidNightToDay"]==False and d["requiredD"]==2'
req h.jar PUT /rules '{"requiredD":2,"requiredE":2,"requiredN":2,"maxConsecutiveNights":3,"maxConsecutiveWorkDays":5,"forbidNightToDay":true}'; ok 200 "규칙 개별 수정"
req n.jar PUT /rules '{"requiredD":0,"requiredE":0,"requiredN":0,"maxConsecutiveNights":3,"maxConsecutiveWorkDays":5,"forbidNightToDay":true}'; ok 403 "일반 간호사 규칙 수정 불가"
req h.jar POST /holidays '{"date":"'"$YM-09"'","name":"한글날"}'; ok 201 "공휴일 등록"
req h.jar POST /holidays '{"date":"'"$YM-09"'","name":"중복"}'; ok 409 "공휴일 중복 거부"
req n.jar GET "/holidays?yearMonth=$YM"; ok 200 "공휴일 조회" 'len(d)==1'

echo "■ 신청 (REQ)"
req n.jar POST /requests '{"type":"ANNUAL_LEAVE","date":"'"$D_L0"'"}'; ok 201 "연차 신청"; LV=$(val 'd["id"]')
req n.jar POST /requests '{"type":"WISH_OFF","date":"'"$D_L1"'","reasonCode":"FAMILY"}'; ok 201 "희망 오프 신청"; WO=$(val 'd["id"]')
req n.jar POST /requests '{"type":"WISH_OFF","date":"'"$D_L2"'","reasonCode":"NOPE"}'; ok 400 "잘못된 사유 코드 거부"
req n.jar POST /requests '{"type":"WISH_DUTY","date":"'"$D_L3"'","duty":"O"}'; ok 400 "희망 근무는 D/E/N만"
req n.jar POST /requests '{"type":"WISH_DUTY","date":"'"$D_L4"'","duty":"D"}'; ok 201 "희망 근무 신청"; WD=$(val 'd["id"]')
req h.jar GET /requests; ok 200 "관리 조회 (기본 PENDING)" 'd["total"]==3'
req n.jar GET /requests; ok 403 "일반 간호사 관리 조회 불가"
req h.jar POST /requests/$LV/approve; ok 200 "연차 승인"
req h.jar POST /requests/$WO/approve; ok 200 "희망 오프 승인"
req h.jar POST /requests/$WD/reject '{"reason":"인원 부족"}'; ok 200 "희망 근무 반려" 'd["rejectReason"]=="인원 부족"'
req h.jar POST /requests/$LV/approve; ok 409 "이미 처리된 신청 재처리 불가"
req n.jar GET "/requests/me?yearMonth=$YM"; ok 200 "본인 신청 목록" 'd["total"]==3'
req n.jar POST /requests '{"type":"ANNUAL_LEAVE","date":"'"$D_L0"'"}'; ok 409 "[리뷰#4] 같은 날짜 중복 신청 409"
req n.jar POST /requests '{"type":"ANNUAL_LEAVE","date":"2020-01-01"}'; ok 400 "[리뷰#4] 지난 날짜 신청 400"
req n.jar POST /requests '{"type":"ANNUAL_LEAVE","date":"'"$YM-26"'","reasonCode":"아무거나"}'; ok 400 "[리뷰#3] 연차도 사유는 코드값만"
req n.jar GET "/requests/me?yearMonth=abc"; ok 400 "[리뷰#7] 잘못된 연월 → 400 (500 아님)"
req n.jar GET "/nurses?role=KING"; ok 400 "[리뷰#7] 잘못된 enum 파라미터 → 400"
req n.jar POST /requests '{bad json'; ok 400 "[리뷰#7] 깨진 JSON → 400"

echo "■ 근무표 (SCH)"
req h.jar POST /schedules/$YM; ok 201 "빈 근무표 생성 + 연차 AL 선반영" "[r for r in d['rows'] if r['nurseId']==$NID][0]['cells'].get('$D_L0')=='AL'"
req h.jar GET /schedules/$YM; ok 200 "임신자 N 불가 표시 (수간호사만)" "[r for r in d['rows'] if r['nurseId']==$PREG][0]['nightBlocked']==True"
req h.jar POST /schedules/$YM; ok 409 "같은 달 중복 생성 불가"
req h.jar POST /schedules/2026-13; ok 400 "잘못된 연월 거부"
req n.jar GET /schedules/$YM; ok 404 "일반 간호사는 초안 못 봄 (404)"
req h.jar PATCH /schedules/$YM/cells "[{\"nurseId\":$CH,\"date\":\"$YM-01\",\"duty\":\"N\"},{\"nurseId\":$NEWID,\"date\":\"$YM-02\",\"duty\":\"N\"},{\"nurseId\":$PREG,\"date\":\"$YM-03\",\"duty\":\"N\"}]"
ok 200 "일괄 편집 + 위반 검출 (차지 N·신입 단독·임신 N)" '{"CHARGE_DAY_ONLY","NEW_NIGHT_ALONE","PREGNANT_NIGHT"} <= {v["ruleId"] for v in d}'
req h.jar PATCH /schedules/$YM/cells "[{\"nurseId\":$CH,\"date\":\"$YM-05\",\"duty\":\"AL\"}]"; ok 400 "AL 수동 입력 불가"
req h.jar PUT /nurses/$G9 "{\"name\":\"일반9\",\"dutyRole\":\"GENERAL\",\"status\":\"ACTIVE\",\"joinedAt\":\"2025-01-01\",\"careerMonths\":108,\"skillLevel\":3,\"affiliationStart\":\"$YM-15\"}"
req h.jar PATCH /schedules/$YM/cells "[{\"nurseId\":$G9,\"date\":\"$YM-01\",\"duty\":\"D\"}]"; ok 400 "소속 기간 밖 셀 편집 불가 (월 중간 입사)"
req h.jar PUT /nurses/$G9 "$(printf "$NURSE" 일반9 GENERAL ACTIVE 108)"
req h.jar PATCH /schedules/$YM/cells "[{\"nurseId\":99999,\"date\":\"$YM-01\",\"duty\":\"D\"}]"; ok 404 "없는 간호사 404"
req h.jar POST /schedules/$YM/confirm; ok 409 "하드 위반 있으면 확정 차단" 'd["detail"] and all(v["severity"]=="HARD" for v in d["detail"])'

echo "■ 동시 편집 잠금 (SCH-13)"
req h.jar POST /ward/head-transfer "{\"nurseId\":$NID,\"mode\":\"GRANT\"}"; ok 200 "수간호사 권한 추가 부여"
req h.jar POST /schedules/$YM/lock; ok 200 "잠금 획득" 'd["name"]=="김수간"'
req n.jar PATCH /schedules/$YM/cells "[{\"nurseId\":$CH,\"date\":\"$YM-01\",\"duty\":\"D\"}]"; ok 409 "남이 잠근 근무표 편집 불가" '"편집 중" in d["message"]'
req n.jar POST "/schedules/$YM/lock?force=true"; ok 200 "강제로 가져오기" 'd["name"]=="이간호"'
req h.jar POST /schedules/$YM/generate; ok 409 "빼앗긴 쪽은 생성 불가"

echo "■ 자동 생성 (GEN-01·05·06)"
req n.jar PATCH /schedules/$YM/cells "[$FIXCELLS]"
req n.jar POST /schedules/$YM/generate "{\"fixed\":[$FIXREFS]}"
ok 200 "자동 생성: 하드 위반 0 + 희망 오프 반영" 'd["solved"]==True and d["wishOffRate"]==1.0'
req n.jar GET /schedules/$YM
ok 200 "고정 셀·연차 유지" "(lambda c: c['$YM-10']=='D' and c['$D_L0']=='AL')([r for r in d['rows'] if r['nurseId']==$NID][0]['cells'])"
ok 200 "커버리지·통계·잠금 표시" "d['coverage']['$YM-15']['D']>=2 and len(d['stats'])==14 and d['lock']['name']=='이간호'"

echo "■ 엑셀 (SCH-09·11, 명단)"
CODE=$(curl -s -b n.jar -o s.xlsx -w '%{http_code}' $U/schedules/$YM/export); echo '{}' > out.json; ok 200 "근무표 엑셀 내보내기"
CODE=$(curl -s -b n.jar -F file=@s.xlsx -o out.json -w '%{http_code}' $U/schedules/$YM/import/preview)
ok 200 "내보낸 파일 가져오기 미리보기" 'len(d["cells"])>=13*25 and d["codes"].get("D")=="D"'
req n.jar POST /schedules/$YM/import/apply "[{\"nurseId\":$NID,\"date\":\"$YM-10\",\"duty\":\"D\"}]"; ok 200 "가져오기 반영"
echo hi > bad.xlsx; CODE=$(curl -s -b n.jar -F file=@bad.xlsx -o out.json -w '%{http_code}' $U/schedules/$YM/import/preview); ok 400 "깨진 파일 거부"
CODE=$(curl -s -b h.jar -o nurses.xlsx -w '%{http_code}' $U/nurses/export); echo '{}' > out.json; ok 200 "명단 내보내기"
CODE=$(curl -s -b h.jar -F file=@nurses.xlsx -o out.json -w '%{http_code}' $U/nurses/import/preview)
ok 200 "명단 재업로드 미리보기 (중복 이름 경고)" 'len(d)==14 and all(r["errors"]==[] and "이미 등록된 이름입니다" in r["warnings"] for r in d)'
CODE=$(curl -s -b h.jar -F file=@$FIXTURE -o out.json -w '%{http_code}' $U/nurses/import/preview)
python3 -c "import json;print(json.dumps([r['form'] for r in json.load(open('out.json')) if not r['errors']]))" > ok.json
ok 200 "새 명단 미리보기 (오류 행 표시)" 'sum(1 for r in d if r["errors"])==1'
req h.jar POST /nurses/import/apply "$(cat ok.json)"; ok 201 "명단 일괄 등록" 'len(d)==4'

echo "■ 확정·알림 (SCH-12·REQ-06)"
req n.jar POST /requests '{"type":"WISH_OFF","date":"'"$YM-27"'","reasonCode":"ETC"}'; PEND=$(val 'd["id"]')
req n.jar POST /schedules/$YM/confirm; ok 409 "[리뷰#2] 대기 중인 신청이 있으면 확정 차단" "d['detail']==[$PEND]"
req n.jar POST /requests/$PEND/reject '{"reason":"확정 예정"}'
req n.jar POST /schedules/$YM/confirm; ok 200 "확정"
req n.jar POST /requests '{"type":"WISH_OFF","date":"'"$D_L5"'","reasonCode":"ETC"}'; ok 409 "확정된 월에는 신청 불가"
req h.jar POST /nurses/$HID/retire '{"affiliationEnd":"2099-12-31"}'; ok 200 "수간호사 2명이면 퇴사 가능" 'type(d["violations"])==list'
CODE=$(curl -s -o out.json -w '%{http_code}' -b h.jar $U/me); ok 200 "퇴사자 me: 소속 없음" 'd["wardId"] is None'
req n.jar GET /notifications/me; ok 200 "알림: 가입승인·확정" '{"JOIN_APPROVED","SCHEDULE_CONFIRMED","REQUEST_DECIDED"} <= {x["type"] for x in d["page"]["content"]}'
FID=$(val 'd["page"]["content"][0]["id"]'); UNREAD=$(val 'd["unread"]')
req n.jar POST /notifications/me/$FID/read; req n.jar GET /notifications/me; ok 200 "읽음 처리" "[x for x in d['page']['content'] if x['id']==$FID][0]['read']==True"
req n.jar PUT /notifications/me/settings '{"muted":["SCHEDULE_UNCONFIRMED"]}'; ok 200 "알림 종류 끄기"
req n.jar POST /schedules/$YM/unconfirm '{"reason":""}'; ok 400 "취소 사유 필수"
req n.jar POST /schedules/$YM/unconfirm '{"reason":"인원 변경"}'; ok 200 "확정 취소"
req n.jar GET /notifications/me; ok 200 "꺼둔 종류는 알림 안 옴" '"SCHEDULE_UNCONFIRMED" not in {x["type"] for x in d["page"]["content"]}'
req n.jar POST /schedules/$YM/confirm; ok 200 "재확정"

echo "■ 근무 전날 리마인드 (10초마다 실행 설정)"
if [ "$TM" != "$YM" ]; then
  req n.jar POST /schedules/$TM; ok 201 "'내일'이 속한 달($TM) 근무표 생성"
  req n.jar PATCH /schedules/$TM/cells "[$TFIXCELLS]"
  req n.jar POST /schedules/$TM/generate "{\"fixed\":[$TFIXREFS]}"; ok 200 "자동 생성 (하드 위반 0)" 'd["solved"]==True'
  req n.jar POST /schedules/$TM/confirm; ok 200 "확정"
fi
sleep 12
req n.jar GET /notifications/me; ok 200 "내일($D_T) D 근무 리마인드 수신" "any(x['type']=='DUTY_REMINDER' and '$D_T' in x['message'] for x in d['page']['content'])"

echo "■ 보안 (SEC-02·03)"
req n.jar POST /auth/login '{"email":"nurse@x.com","password":"pass1234"}'; ok 200 "로그인"
rm -f o.jar; req o.jar POST /auth/signup '{"name":"타병동","email":"other@x.com","password":"pass1234","agreeTerms":true}'
req o.jar POST /wards '{"name":"9","hospital":"x","requiredD":1,"requiredE":1,"requiredN":1,"preset":"MINIMAL"}'
req o.jar PUT /nurses/$CH "$(printf "$NURSE" 해킹 GENERAL ACTIVE 1)"; ok 404 "타 병동 간호사 수정 → 404"
req o.jar GET /schedules/$YM; ok 404 "타 병동 근무표 → 404"
req o.jar POST /requests/$LV/approve; ok 404 "타 병동 신청 승인 → 404"
req o.jar POST /notifications/me/$FID/read; ok 404 "남의 알림 읽음 처리 → 404"
req o.jar GET "/audit-logs?size=100"; ok 200 "타 병동 감사 로그 안 보임" 'all(x["action"] in ("WARD_CREATED","CODE_ISSUED") for x in d["content"])'
req n.jar GET "/audit-logs?size=200"
ok 200 "감사 로그 기록 대상" '{"LOGIN","WARD_CREATED","CODE_REISSUED","JOIN_REQUESTED","JOIN_APPROVED","HEAD_GRANT","NURSE_REGISTERED","NURSE_RETIRED","RULES_CHANGED","SCHEDULE_CREATED","SCHEDULE_GENERATED","SCHEDULE_CONFIRMED","SCHEDULE_UNCONFIRMED","EXCEL_EXPORTED","EXCEL_IMPORTED","NURSE_EXPORTED","NURSE_IMPORTED","REQUEST_APPROVED","REQUEST_REJECTED","LOCK_FORCED"} <= {x["action"] for x in d["content"]}'
req n.jar GET "/audit-logs?action=LOCK_FORCED"; ok 200 "감사 로그 필터" 'd["total"]==1'
req n.jar POST /nurses "$(printf "$NURSE" 박연결 GENERAL ACTIVE 30)"; LINK=$(val 'd["nurse"]["id"]')
rm -f l.jar; req l.jar POST /auth/signup '{"name":"박연결","email":"link@x.com","password":"pass1234","agreeTerms":true}'
req l.jar POST /ward/join "{\"code\":\"$CODE2\"}"
req n.jar GET /ward/join-requests; ok 200 "[리뷰#5] 가입 대기에 같은 이름의 미연결 간호사 후보 표시" "[j for j in d if j['name']=='박연결'][0]['candidates']==[$LINK]"
JID2=$(val "[j for j in d if j['name']=='박연결'][0]['id']")
req n.jar POST /ward/join-requests/$JID2/approve "{\"nurseId\":$LINK}"; ok 200 "[리뷰#5] 기존 간호사 행에 계정 연결"
req l.jar GET /me; ok 200 "[리뷰#5] 새 행 없이 기존 간호사로 소속" "d['nurseId']==$LINK"
req n.jar POST /nurses "$(printf "$NURSE" 동명 GENERAL ACTIVE 10)"; req n.jar POST /nurses "$(printf "$NURSE" 동명 GENERAL ACTIVE 20)"
curl -s -b n.jar -o s2.xlsx $U/schedules/$YM/export
CODE=$(curl -s -b n.jar -F file=@s2.xlsx -o out.json -w '%{http_code}' $U/schedules/$YM/import/preview)
ok 200 "[리뷰#6] 동명이인은 자동 매칭하지 않고 후보 제시" '[(r["nurseId"], len(r["candidates"])) for r in d["rows"] if r["name"]=="동명"]==[(None,2),(None,2)]'
for i in 1 2 3 4 5; do req o.jar POST /auth/login '{"email":"link@x.com","password":"wrong1234"}'; done
req o.jar POST /auth/login '{"email":"link@x.com","password":"pass1234"}'; ok 429 "[리뷰#8] 로그인 5회 실패 후 차단 (맞는 비밀번호도)"
req n.jar POST /auth/logout; ok 204 "로그아웃"
req n.jar GET /me; ok 401 "로그아웃 후 401"

echo
echo "결과: 통과 $PASS / 실패 $FAIL"
[ $FAIL = 0 ]
