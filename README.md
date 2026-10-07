# 병동 근무표 백엔드

간호사 근무표 자동 생성 서비스 백엔드. FastAPI / Python 3.12 / PostgreSQL. API 명세서 v1.0 기준.

## 빠른 실행 (Docker만 있으면 됨)
```bash
docker compose up --build -d     # PostgreSQL + API
```
- API 문서: http://localhost:8000/docs (Swagger)
- 로그: `docker compose logs -f api` / 중지: `docker compose down` / DB까지 초기화: `docker compose down -v`
- PostgreSQL 포트는 호스트에 열지 않는다(로컬 5432와 충돌 방지). 직접 접속: `docker compose exec postgres psql -U nurs`

## 로컬 개발 (uv)
```bash
uv sync                                         # 의존성 설치 (brew install uv)
uv run uvicorn app.main:app --reload            # SQLite 파일(nurs-local.db), http://localhost:8000/docs
uv run pytest
```

## 구조 (3-Layered)
- `app/presentation` 라우트·요청 검증·인증 쿠키·요청 단위 트랜잭션
- `app/business` 서비스 (권한·병동 범위 검증, 감사 로그, 알림)
- `app/persistence` SQLAlchemy 모델
- `app/domain` 순수 규칙. FastAPI·DB에 의존하지 않는다
