# 운영 배포

## 준비

1. DNS에서 서비스 도메인과 API 도메인을 서버로 연결합니다.
2. `.env.production.example`을 `.env.production`으로 복사하고 실제 도메인을 입력합니다.
3. 방화벽에서는 80·443 포트만 외부에 공개합니다.

```bash
docker compose --env-file .env.production -f compose.production.yml up -d --build
```

Caddy가 HTTPS 인증서를 자동 발급하고, API는 외부에 직접 노출하지 않고 HTTPS 프록시 뒤에서 동작합니다. SQLite 데이터와 인증서 데이터는 Docker 볼륨에 보존됩니다.

## 운영 모드 차이

- 데모 계정과 `WARD_DEV_USER_ID` 인증 우회를 사용할 수 없습니다.
- 세션 쿠키는 HTTPS에서만 전달됩니다.
- 허용 프론트 도메인을 `WARD_ALLOWED_ORIGINS`로 제한합니다.
- 자동 편성은 백그라운드 작업으로 실행됩니다.
- API 로그는 JSON으로 출력됩니다.
- `/health`는 프로세스 상태, `/ready`는 데이터베이스 준비 상태를 확인합니다.
- 편집 잠금은 기본 30분 뒤 만료됩니다.

## 백업

호스트 또는 별도 백업 작업에서 다음 스크립트를 주기적으로 실행합니다.

```bash
WARD_DB_PATH=/data/ward_scheduler.db \
WARD_BACKUP_DIR=/backups \
python3 scripts/backup_db.py
```

기본적으로 최근 14개 백업을 유지하고 생성 직후 SQLite 무결성 검사를 수행합니다. 운영에서는 백업 파일을 별도 저장소로 복제하고 복원 훈련을 정기적으로 해야 합니다.
