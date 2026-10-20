"""AUTH-01·02. 액세스·리프레시 토큰은 불투명 난수이며 DB에는 해시만 저장한다"""
import secrets
import uuid
from datetime import timedelta

import bcrypt
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.business import audit, members, wards
from app.business.common import ApiException, RateLimiter, conflict, sha256
from app.domain.model import AccountStatus
from app.persistence.db import now
from app.persistence.models import SessionToken, User

ACCESS_TTL = timedelta(minutes=30)
REFRESH_TTL = timedelta(days=14)  # 🔶 권장값
# 이메일별 로그인 실패 15분에 5회까지. 성공하면 초기화
_login_failures = RateLimiter(5, timedelta(minutes=15))


def signup(db: Session, name: str, email: str, password: str) -> dict:
    """role·wardId는 받지 않는다. 클라이언트가 보내도 무시된다 (AUTH-01 보안)"""
    email = email.strip().lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise conflict("EMAIL_ALREADY_EXISTS", "이미 가입된 이메일입니다")
    u = User(email=email, name=name.strip(), password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode())
    db.add(u)
    db.flush()
    return summary(u)


def login(db: Session, email: str, password: str) -> tuple[dict, dict]:
    email = email.strip().lower()
    _login_failures.check(email)
    u = db.scalar(select(User).where(User.email == email))
    if u is None or not bcrypt.checkpw(password.encode(), u.password_hash.encode()):
        _login_failures.record(email)
        raise ApiException(401, "INVALID_CREDENTIALS", "이메일 또는 비밀번호가 올바르지 않습니다")
    if u.status == AccountStatus.DISABLED:
        raise ApiException(423, "ACCOUNT_DISABLED", "비활성화된 계정입니다")
    _login_failures.reset(email)
    audit.log(db, _ward_of(db, u.id), u.id, "LOGIN", "USER", u.id)
    return summary(u), _issue(db, u.id)


def refresh(db: Session, refresh_token: str | None) -> tuple[dict, dict]:
    """리프레시 토큰 회전: 쓴 토큰 쌍은 폐기하고 새 쌍을 발급한다. 폐기된 토큰을 다시 쓰면 401"""
    s = db.scalar(select(SessionToken).where(SessionToken.refresh_hash == sha256(refresh_token or "")))
    if s is None or s.refresh_expires_at < now():
        raise ApiException(401, "REFRESH_TOKEN_INVALID", "다시 로그인하세요")
    u = db.get(User, s.user_id)
    db.delete(s)
    if u.status == AccountStatus.DISABLED:
        raise ApiException(423, "ACCOUNT_DISABLED", "비활성화된 계정입니다")
    return summary(u), _issue(db, u.id)


def authenticate(db: Session, access_token: str | None) -> uuid.UUID:
    s = db.scalar(select(SessionToken).where(SessionToken.access_hash == sha256(access_token or "")))
    if s is None or s.access_expires_at < now():
        raise ApiException(401, "UNAUTHENTICATED", "로그인이 필요합니다")
    return s.user_id


def logout(db: Session, user_id: uuid.UUID, access_token: str | None) -> None:
    db.execute(delete(SessionToken).where(SessionToken.access_hash == sha256(access_token or "")))
    audit.log(db, _ward_of(db, user_id), user_id, "LOGOUT", "USER", user_id)


def me(db: Session, user_id: uuid.UUID) -> dict:
    """MeResponse. membershipRequest는 소속 전 승인 대기 화면용 (가장 최근 가입 신청)"""
    u = db.get(User, user_id)
    n = members.membership(db, user_id)
    return {"user": summary(u), "membership": members.view(n) if n else None,
            "ward": wards.view(db, n.ward_id) if n else None,
            "membershipRequest": None if n else wards.latest_request(db, user_id)}


def summary(u: User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "accountStatus": u.status}


def _issue(db: Session, user_id: uuid.UUID) -> dict:
    tokens = {k: secrets.token_urlsafe(32) for k in ("access", "refresh", "csrf")}
    db.add(SessionToken(access_hash=sha256(tokens["access"]), refresh_hash=sha256(tokens["refresh"]), user_id=user_id,
                        access_expires_at=now() + ACCESS_TTL, refresh_expires_at=now() + REFRESH_TTL))
    return tokens


def _ward_of(db: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    n = members.membership(db, user_id)
    return n.ward_id if n else None
