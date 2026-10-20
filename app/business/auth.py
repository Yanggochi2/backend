"""AUTH-01·05. 액세스·리프레시·CSRF 토큰은 불투명 난수이며 DB에는 해시만 저장한다"""
import secrets
import uuid
from datetime import timedelta

import bcrypt
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.business import audit, members, wards
from app.business.common import ApiException, RateLimiter, conflict, sha256
from app.domain.model import AccountStatus
from app.persistence.db import now
from app.persistence.models import IdempotencyRecord, Notification, SessionToken, User

ACCESS_TTL = timedelta(minutes=30)
REFRESH_TTL = timedelta(days=14)  # 🔶 권장값
# 이메일별 로그인 실패 15분에 5회까지. 성공하면 초기화
_login_failures = RateLimiter(5, timedelta(minutes=15))
# 가입은 클라이언트 IP별 시간당 20회까지 (409로 가입 여부를 대량 조회하는 것 방지)
# ponytail: 리버스 프록시 뒤라면 uvicorn --proxy-headers 없이는 모든 요청이 프록시 IP 하나로 묶인다
_signups = RateLimiter(20, timedelta(hours=1))
# 없는 이메일에도 같은 시간만큼 bcrypt를 돌려 응답 시간으로 가입 여부가 드러나지 않게 한다
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt())


def signup(db: Session, name: str, email: str, password: str, client: str) -> tuple[dict, dict]:
    """
    role·wardId는 받지 않는다. 클라이언트가 보내도 무시된다 (AUTH-01 보안).
    가입과 동시에 로그인 세션을 발급해 바로 소속 선택 화면으로 갈 수 있게 한다
    """
    _signups.check(client)
    _signups.record(client)
    email = email.strip().lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise conflict("EMAIL_ALREADY_EXISTS", "이미 가입된 이메일입니다")
    u = User(email=email, name=name.strip(), password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode())
    db.add(u)
    db.flush()
    audit.log(db, None, u.id, "SIGNUP", "USER", u.id)
    return summary(u), _issue(db, u.id)


def login(db: Session, email: str, password: str) -> tuple[dict, dict]:
    email = email.strip().lower()
    _login_failures.check(email)
    u = db.scalar(select(User).where(User.email == email))
    ok = bcrypt.checkpw(password.encode(), u.password_hash.encode() if u else _DUMMY_HASH)
    if u is None or not ok:
        _login_failures.record(email)
        raise ApiException(401, "INVALID_CREDENTIALS", "이메일 또는 비밀번호가 올바르지 않습니다")
    if u.status == AccountStatus.DISABLED:
        raise ApiException(423, "ACCOUNT_DISABLED", "비활성화된 계정입니다")
    _login_failures.reset(email)
    audit.log(db, _ward_of(db, u.id), u.id, "LOGIN", "USER", u.id)
    return summary(u), _issue(db, u.id)


def refresh(db: Session, refresh_token: str | None) -> tuple[dict, dict]:
    """
    리프레시 토큰 회전: 쓴 토큰 묶음은 회전됨으로 표시하고 같은 계열로 새 묶음을 발급한다.
    회전된 토큰이 다시 오면 탈취로 보고 그 계열 전체(공격자·피해자 세션 모두)를 폐기한다
    """
    s = db.scalar(select(SessionToken).where(SessionToken.refresh_hash == sha256(refresh_token or "")))
    if s is None or s.refresh_expires_at < now():
        raise ApiException(401, "REFRESH_TOKEN_INVALID", "다시 로그인하세요")
    if s.rotated:
        db.execute(delete(SessionToken).where(SessionToken.family_id == s.family_id))
        audit.log(db, _ward_of(db, s.user_id), s.user_id, "REFRESH_TOKEN_REUSED", "USER", s.user_id)
        db.commit()  # 아래 예외로 요청 트랜잭션이 롤백되어도 폐기는 남아야 한다
        raise ApiException(401, "REFRESH_TOKEN_INVALID", "다시 로그인하세요")
    u = db.get(User, s.user_id)
    if u.status == AccountStatus.DISABLED:
        raise ApiException(423, "ACCOUNT_DISABLED", "비활성화된 계정입니다")
    s.rotated = True
    return summary(u), _issue(db, u.id, s.family_id)


def authenticate(db: Session, access_token: str | None) -> uuid.UUID:
    s = db.scalar(select(SessionToken).where(SessionToken.access_hash == sha256(access_token or "")))
    if s is None or s.rotated or s.access_expires_at < now():
        raise ApiException(401, "UNAUTHENTICATED", "로그인이 필요합니다")
    return s.user_id


def verify_csrf(db: Session, access_token: str | None, refresh_token: str | None, header: str | None) -> None:
    """CSRF 토큰은 세션에 묶인다: 요청에 실린 액세스·리프레시 토큰 중 하나의 세션이 발급한 값이어야 한다"""
    found = header and db.scalar(select(SessionToken.id).where(
        SessionToken.csrf_hash == sha256(header),
        or_(SessionToken.access_hash == sha256(access_token or ""),
            SessionToken.refresh_hash == sha256(refresh_token or ""))).limit(1))
    if not found:
        raise ApiException(403, "CSRF_TOKEN_INVALID", "CSRF 토큰이 없거나 일치하지 않습니다")


def logout(db: Session, access_token: str | None, refresh_token: str | None) -> None:
    """
    액세스가 만료됐어도 리프레시 토큰으로 세션을 찾아 로그아웃한다 (AUTH-05 ③).
    회전으로 이어진 같은 계열 세션을 모두 폐기한다. 세션이 없으면 아무것도 하지 않는다
    """
    s = db.scalar(select(SessionToken).where(or_(SessionToken.access_hash == sha256(access_token or ""),
                                                 SessionToken.refresh_hash == sha256(refresh_token or ""))).limit(1))
    if s is None:
        return
    db.execute(delete(SessionToken).where(SessionToken.family_id == s.family_id))
    audit.log(db, _ward_of(db, s.user_id), s.user_id, "LOGOUT", "USER", s.user_id)


def withdraw(db: Session, user_id: uuid.UUID, password: str) -> None:
    """
    계정 탈퇴. 소속이 있으면 병동 탈퇴를 먼저 하고(마지막 수간호사면 409), 세션·알림·멱등 응답을 지운 뒤
    계정 정보를 익명화한다. 지난 근무표의 이름은 간호사 행에 기록으로 남는다. 같은 이메일로 다시 가입할 수 있다
    """
    u = db.get(User, user_id)
    if not bcrypt.checkpw(password.encode(), u.password_hash.encode()):
        raise ApiException(401, "INVALID_CREDENTIALS", "비밀번호가 올바르지 않습니다")
    ward_id = _ward_of(db, user_id)
    if ward_id:
        wards.leave(db, user_id, "계정 탈퇴")
    wards.cancel_pending(db, user_id)
    for model in (SessionToken, Notification, IdempotencyRecord):
        db.execute(delete(model).where(model.user_id == user_id))
    u.email, u.name = f"deleted-{u.id}@deleted.invalid", "탈퇴한 사용자"
    u.password_hash = bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt()).decode()
    u.status, u.notification_settings = AccountStatus.WITHDRAWN, {}
    audit.log(db, ward_id, user_id, "ACCOUNT_DELETED", "USER", user_id)


def me(db: Session, user_id: uuid.UUID) -> dict:
    """MeResponse. membershipRequest는 소속 전 승인 대기 화면용 (가장 최근 가입 신청)"""
    u = db.get(User, user_id)
    n = members.membership(db, user_id)
    return {"user": summary(u), "membership": members.view(n) if n else None,
            "ward": wards.view(db, n.ward_id) if n else None,
            "membershipRequest": None if n else wards.latest_request(db, user_id)}


def summary(u: User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "accountStatus": u.status}


def _issue(db: Session, user_id: uuid.UUID, family_id: uuid.UUID | None = None) -> dict:
    """새 토큰 묶음 발급. 이 사용자의 리프레시까지 만료된 세션 행은 이때 정리한다"""
    db.execute(delete(SessionToken).where(SessionToken.user_id == user_id, SessionToken.refresh_expires_at < now()))
    tokens = {k: secrets.token_urlsafe(32) for k in ("access", "refresh", "csrf")}
    db.add(SessionToken(family_id=family_id or uuid.uuid4(), access_hash=sha256(tokens["access"]),
                        refresh_hash=sha256(tokens["refresh"]), csrf_hash=sha256(tokens["csrf"]), user_id=user_id,
                        access_expires_at=now() + ACCESS_TTL, refresh_expires_at=now() + REFRESH_TTL))
    return tokens


def _ward_of(db: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    n = members.membership(db, user_id)
    return n.ward_id if n else None
