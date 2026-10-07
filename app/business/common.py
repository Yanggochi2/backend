import hashlib
import math
import threading
from collections import deque
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


class ApiException(Exception):
    """서비스 계층 예외. presentation에서 {"error": {code, message, traceId, ...}} 응답으로 변환된다"""

    def __init__(self, status: int, code: str, message: str, details=None, field_errors: list | None = None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.details, self.field_errors = details, field_errors


def bad_request(code: str, m: str, field: str | None = None) -> ApiException:
    return ApiException(400, code, m, field_errors=[{"field": field, "reason": code}] if field else None)


def reason_required(field: str = "reason") -> ApiException:
    return bad_request("REASON_REQUIRED", "사유를 입력하세요", field)


def forbidden(m: str = "권한이 없습니다") -> ApiException:
    return ApiException(403, "FORBIDDEN", m)


def not_found(code: str = "RESOURCE_NOT_FOUND") -> ApiException:
    """1.2: 다른 병동 리소스·조회 권한 밖은 존재 여부를 숨기고 404"""
    return ApiException(404, code, "찾을 수 없습니다")


def conflict(code: str, m: str, details=None) -> ApiException:
    return ApiException(409, code, m, details)


def unprocessable(code: str, m: str, details=None) -> ApiException:
    return ApiException(422, code, m, details)


def require_reason(reason: str | None, field: str = "reason") -> str:
    if not reason or not reason.strip():
        raise reason_required(field)
    return reason.strip()


def today_kst() -> date:
    return datetime.now(KST).date()


def iso(v: date | datetime | None) -> str | None:
    """date는 2026-10-01, datetime(UTC naive)은 2026-10-06T03:58:01.471537Z"""
    if v is None:
        return None
    return v.isoformat() + "Z" if isinstance(v, datetime) else v.isoformat()


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def page(content: list, page_no: int, size: int, total: int, **extra) -> dict:
    """1.3 목록 응답 {data, meta: PageMeta}"""
    return {"data": content, "meta": {"page": page_no, "size": size, "totalElements": total,
                                      "totalPages": math.ceil(total / size) if size else 0, **extra}}


def parse_sort(sort: str | None, allowed: set[str], default: str) -> tuple[str, bool]:
    """1.4 field,asc|desc → (field, desc). 허용되지 않은 필드는 400 INVALID_FILTER"""
    field, _, direction = (sort or default).partition(",")
    direction = direction or "asc"
    if field not in allowed or direction not in ("asc", "desc"):
        raise bad_request("INVALID_FILTER", f"정렬은 {sorted(allowed)} 중 field,asc|desc 형식입니다", "sort")
    return field, direction == "desc"


def parse_month(ym: str, field: str = "yearMonth") -> date:
    """yyyy-MM → 그 달 1일"""
    try:
        y, m = ym.split("-")
        if len(y) != 4 or len(m) != 2:
            raise ValueError
        return date(int(y), int(m), 1)
    except ValueError:
        raise bad_request("INVALID_YEAR_MONTH", "연월 형식은 YYYY-MM 입니다", field) from None


def month_end(first: date) -> date:
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


class RateLimiter:
    """
    키별 슬라이딩 윈도우 시도 횟수 제한. 초과하면 429.
    ponytail: 인메모리라 서버 다중화·재시작 시 공유되지 않는다. 공유 저장소로 옮기는 건 Yanggochi2/backend#28
    """

    def __init__(self, max_hits: int, window: timedelta):
        self.max, self.window = max_hits, window
        self.hits: dict[object, deque] = {}
        self.lock = threading.Lock()

    def check(self, key) -> None:
        with self.lock:
            q = self.hits.get(key)
            if not q:
                return
            cutoff = datetime.now() - self.window
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max:
                raise ApiException(429, "RATE_LIMITED", "시도 횟수를 초과했습니다. 잠시 후 다시 시도하세요")

    def record(self, key) -> None:
        with self.lock:
            self.hits.setdefault(key, deque()).append(datetime.now())

    def reset(self, key) -> None:
        with self.lock:
            self.hits.pop(key, None)
