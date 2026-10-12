from __future__ import annotations

import json
import os
import re
import signal
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
from email import policy
from email.parser import BytesParser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections import defaultdict, deque

from .db import Database
from .errors import ApiError
from .features import FeatureWardService


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "WardSchedulerAPI/0.1"

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_PATCH(self):
        self._dispatch()

    def do_PUT(self):
        self._dispatch()

    def do_DELETE(self):
        self._dispatch()

    def do_OPTIONS(self):
        self.send_response(204)
        self._common_headers()
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,X-CSRF-Token,Idempotency-Key,X-Schedule-Lock-Token")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def log_message(self, fmt, *args):
        if self.server.json_logs:
            sys.stderr.write(json.dumps({
                "timestamp": self.log_date_time_string(), "level": "INFO", "message": fmt % args,
                "clientIp": self.client_address[0], "requestId": getattr(self, "_current_trace_id", None),
            }, ensure_ascii=False) + "\n")
        else:
            sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

    @property
    def service(self) -> FeatureWardService:
        return self.server.service

    def _dispatch(self):
        trace_id = str(uuid.uuid4())
        self._current_trace_id = trace_id
        self._idempotency_record_id = None
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            body = self._request_body() if self.command in ("POST", "PATCH", "PUT") else {}

            if self.command == "GET" and path == "/health":
                return self._send(200, {"status": "ok"}, raw=True)
            if self.command == "GET" and path == "/ready":
                with self.service.db.connection() as conn:
                    conn.execute("SELECT 1").fetchone()
                return self._send(200, {"status": "ready"}, raw=True)

            require_prefix = "/api/v1"
            if not path.startswith(require_prefix):
                raise ApiError(404, "RESOURCE_NOT_FOUND", "요청 경로를 찾을 수 없습니다.")
            route = path[len(require_prefix):] or "/"

            if self.command == "POST" and route == "/auth/signup":
                return self._send(201, self.service.signup(body))
            if self.command == "POST" and route == "/auth/login":
                if not self.server.allow_login(self.client_address[0]):
                    raise ApiError(429, "RATE_LIMITED", "로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요.")
                result = self.service.login(body)
                cookie = f"ward_session={result.pop('sessionId')}; HttpOnly; SameSite=Strict; Path=/api/v1; Max-Age=1209600"
                csrf_cookie = f"csrfToken={result['csrfToken']}; SameSite=Strict; Path=/; Max-Age=1209600"
                if self.server.secure_cookies:
                    cookie += "; Secure"
                    csrf_cookie += "; Secure"
                return self._send(200, result, extra_headers=[("Set-Cookie", cookie), ("Set-Cookie", csrf_cookie)])

            identity = self._identity()
            actor_id = identity.get("userId")
            if not actor_id:
                raise ApiError(401, "UNAUTHENTICATED", "인증 세션이 필요합니다.")
            if self.command in ("POST", "PATCH", "PUT", "DELETE") and identity.get("sessionId"):
                if self.headers.get("X-CSRF-Token") != identity.get("csrfToken"):
                    raise ApiError(403, "CSRF_TOKEN_INVALID", "CSRF 토큰이 올바르지 않습니다.")

            idempotency_key = self.headers.get("Idempotency-Key")
            if self.command == "POST" and idempotency_key:
                record_id, replay = self.service.begin_idempotency(actor_id, idempotency_key, self.command, route, body)
                if replay is not None:
                    if replay["status"] == 204:
                        return self._send_empty(204)
                    return self._send(replay["status"], replay["payload"], raw=True)
                self._idempotency_record_id = record_id

            if self.command == "POST" and route == "/auth/logout":
                if identity.get("sessionId"):
                    self.service.logout(identity["sessionId"])
                cookie = "ward_session=; HttpOnly; SameSite=Strict; Path=/api/v1; Max-Age=0"
                if self.server.secure_cookies:
                    cookie += "; Secure"
                csrf_cookie = "csrfToken=; SameSite=Strict; Path=/; Max-Age=0"
                return self._send_empty(204, [("Set-Cookie", cookie), ("Set-Cookie", csrf_cookie)])
            if self.command == "POST" and route == "/auth/refresh":
                require_session = identity.get("sessionId")
                if not require_session:
                    raise ApiError(401, "REFRESH_TOKEN_INVALID", "갱신할 세션이 없습니다.")
                self.service.refresh_session(require_session)
                return self._send(200, {})
            if self.command == "GET" and route == "/me":
                return self._send(200, self.service.get_me(actor_id))
            if self.command == "POST" and route == "/wards":
                return self._send(201, self.service.create_ward(actor_id, body))
            if self.command == "POST" and route == "/ward-membership-requests":
                return self._send(201, self.service.request_membership(actor_id, body))

            if self.command == "GET" and route == "/me/notification-settings":
                return self._send(200, self.service.notification_settings(actor_id))
            if self.command == "PATCH" and route == "/me/notification-settings":
                return self._send(200, self.service.notification_settings(actor_id, body))
            if self.command == "GET" and route == "/me/notifications":
                data, meta = self.service.list_notifications(actor_id, query)
                return self._send(200, data, meta)
            match = re.fullmatch(r"/me/notifications/([^/]+)", route)
            if match and self.command == "PATCH":
                return self._send(200, self.service.mark_notification(actor_id, match.group(1), body))

            if self.command == "GET" and route == "/wards/me":
                return self._send(200, self.service.get_ward(actor_id))
            if self.command == "POST" and route == "/wards/me/nurses":
                return self._send(201, self.service.create_nurse(actor_id, body))
            if self.command == "GET" and route == "/wards/me/nurses":
                data, meta = self.service.list_nurses(actor_id, query)
                return self._send(200, data, meta)
            if self.command == "GET" and route == "/wards/me/join-code":
                return self._send(200, self.service.get_join_code(actor_id))
            if self.command == "POST" and route == "/wards/me/join-code/rotate":
                return self._send(200, self.service.rotate_join_code(actor_id))
            if self.command == "GET" and route == "/wards/me/membership-requests":
                data, meta = self.service.list_membership_requests(actor_id, query)
                return self._send(200, data, meta)

            match = re.fullmatch(r"/wards/me/membership-requests/([^/]+)/(approve|reject)", route)
            if match and self.command == "POST":
                return self._send(200, self.service.process_membership_request(
                    actor_id, match.group(1), match.group(2) == "approve", body.get("reason")
                ))

            match = re.fullmatch(r"/wards/me/head-nurses/([^/]+)/grant", route)
            if match and self.command == "POST":
                return self._send(200, self.service.grant_head_nurse(actor_id, match.group(1)))
            if route == "/wards/me/head-nurse-transfer" and self.command == "POST":
                return self._send(200, self.service.transfer_head_nurse(actor_id, body.get("targetNurseId")))

            match = re.fullmatch(r"/wards/me/nurses/([^/]+)", route)
            if match and self.command == "GET":
                return self._send(200, self.service.get_nurse(actor_id, match.group(1)))
            if match and self.command == "PATCH":
                return self._send(200, self.service.update_nurse(actor_id, match.group(1), body))

            match = re.fullmatch(r"/wards/me/nurses/([^/]+)/retire", route)
            if match and self.command == "POST":
                return self._send(200, self.service.retire_nurse(actor_id, match.group(1), body.get("affiliationEnd")))

            if route == "/wards/me/rules" and self.command == "GET":
                return self._send(200, self.service.list_rules(actor_id))
            match = re.fullmatch(r"/wards/me/rules/([^/]+)", route)
            if match and self.command == "PATCH":
                return self._send(200, self.service.patch_rule(actor_id, match.group(1), body))
            match = re.fullmatch(r"/wards/me/rule-presets/([^/]+)/apply", route)
            if match and self.command == "POST":
                return self._send(200, self.service.apply_rule_preset(actor_id, match.group(1)))
            if route == "/wards/me/holidays" and self.command == "GET":
                return self._send(200, self.service.list_holidays(actor_id, query.get("yearMonth")))
            match = re.fullmatch(r"/wards/me/holidays/(\d{4}-\d{2}-\d{2})", route)
            if match and self.command == "PUT":
                return self._send(200, self.service.put_holiday(actor_id, match.group(1), body))
            match = re.fullmatch(r"/wards/me/off-targets/(\d{4}-\d{2})", route)
            if match and self.command == "GET":
                return self._send(200, self.service.get_off_target(actor_id, match.group(1)))
            if match and self.command == "PATCH":
                return self._send(200, self.service.patch_off_target(actor_id, match.group(1), body))

            if route == "/wards/me/schedules" and self.command == "POST":
                return self._send(201, self.service.create_schedule(actor_id, body.get("yearMonth")))
            if route == "/wards/me/schedules" and self.command == "GET":
                return self._send(200, self.service.find_schedule(actor_id, query.get("yearMonth")))

            match = re.fullmatch(r"/wards/me/schedules/([^/]+)", route)
            if match and self.command == "GET":
                return self._send(200, self.service.get_schedule(actor_id, match.group(1)))

            match = re.fullmatch(r"/wards/me/schedules/([^/]+)/cells", route)
            if match and self.command == "PATCH":
                return self._send(200, self.service.patch_cells(
                    actor_id, match.group(1), body, self.headers.get("X-Schedule-Lock-Token")
                ))

            match = re.fullmatch(r"/wards/me/schedules/([^/]+)/coverage", route)
            if match and self.command == "GET":
                return self._send(200, self.service.coverage(actor_id, match.group(1)))

            match = re.fullmatch(r"/wards/me/schedules/([^/]+)/violations", route)
            if match and self.command == "GET":
                items = self.service.violations(actor_id, match.group(1))
                for key in ("severity", "nurseId", "ruleId"):
                    if query.get(key):
                        items = [item for item in items if str(item.get(key)) == query[key]]
                page, size = max(int(query.get("page", 0)), 0), min(max(int(query.get("size", 20)), 1), 100)
                total = len(items)
                return self._send(200, items[page * size:(page + 1) * size], {
                    "page": page, "size": size, "totalElements": total, "totalPages": (total + size - 1) // size
                })

            match = re.fullmatch(r"/wards/me/schedules/([^/]+)/confirm", route)
            if match and self.command == "POST":
                return self._send(200, self.service.confirm_schedule(actor_id, match.group(1)))
            match = re.fullmatch(r"/wards/me/schedules/([^/]+)/confirmation-cancellations", route)
            if match and self.command == "POST":
                return self._send(200, self.service.cancel_schedule_confirmation(actor_id, match.group(1), body.get("reason")))
            match = re.fullmatch(r"/wards/me/schedules/([^/]+)/export\.xlsx", route)
            if match and self.command == "GET":
                content, filename = self.service.export_schedule_xlsx(actor_id, match.group(1))
                return self._send_binary(200, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename)

            match = re.fullmatch(r"/wards/me/schedules/([^/]+)/import-previews", route)
            if match and self.command == "POST":
                file_content = body.get("file")
                filename = body.get("filename")
                if not isinstance(file_content, bytes) or not filename:
                    raise ApiError(400, "VALIDATION_ERROR", "multipart file이 필요합니다.")
                return self._send(202, self.service.create_import_preview(
                    actor_id, match.group(1), filename, file_content
                ))
            match = re.fullmatch(r"/wards/me/schedules/([^/]+)/import-previews/([^/]+)", route)
            if match and self.command == "PATCH":
                return self._send(200, self.service.update_import_preview(actor_id, match.group(1), match.group(2), body))
            match = re.fullmatch(r"/wards/me/schedules/([^/]+)/import-previews/([^/]+)/apply", route)
            if match and self.command == "POST":
                return self._send(200, self.service.apply_import_preview(
                    actor_id, match.group(1), match.group(2), body, self.headers.get("X-Schedule-Lock-Token")
                ))

            match = re.fullmatch(r"/wards/me/schedules/([^/]+)/lock", route)
            if match and self.command == "POST":
                return self._send(201, self.service.acquire_schedule_lock(actor_id, match.group(1)))
            if match and self.command == "DELETE":
                self.service.release_schedule_lock(actor_id, match.group(1), self.headers.get("X-Schedule-Lock-Token"))
                return self._send_empty(204)
            match = re.fullmatch(r"/wards/me/schedules/([^/]+)/lock/takeover", route)
            if match and self.command == "POST":
                return self._send(200, self.service.takeover_schedule_lock(actor_id, match.group(1), body.get("reason")))

            match = re.fullmatch(r"/wards/me/schedules/([^/]+)/generations", route)
            if match and self.command == "POST":
                return self._send(202, self.service.generate_schedule(actor_id, match.group(1), body))

            match = re.fullmatch(r"/wards/me/generations/([^/]+)", route)
            if match and self.command == "GET":
                return self._send(200, self.service.get_generation(actor_id, match.group(1)))
            match = re.fullmatch(r"/wards/me/generations/([^/]+)/stop", route)
            if match and self.command == "POST":
                return self._send(200, self.service.stop_generation(actor_id, match.group(1), bool(body.get("applyBestResult"))))
            match = re.fullmatch(r"/wards/me/generations/([^/]+)/relaxations", route)
            if match and self.command == "POST":
                return self._send(202, self.service.relax_generation(actor_id, match.group(1), body))
            match = re.fullmatch(r"/wards/me/generations/([^/]+)/partial-result/apply", route)
            if match and self.command == "POST":
                return self._send(200, self.service.apply_partial_generation(actor_id, match.group(1), body))

            if route == "/wards/me/requests" and self.command == "POST":
                return self._send(201, self.service.create_work_request(actor_id, body))
            if route == "/wards/me/requests" and self.command == "GET":
                data, meta = self.service.list_work_requests(actor_id, query)
                return self._send(200, data, meta)
            if route == "/wards/me/requests/me" and self.command == "GET":
                data, meta = self.service.list_work_requests(actor_id, query, mine=True)
                return self._send(200, data, meta)
            match = re.fullmatch(r"/wards/me/requests/([^/]+)", route)
            if match and self.command == "GET":
                return self._send(200, self.service.get_work_request(actor_id, match.group(1)))
            match = re.fullmatch(r"/wards/me/requests/([^/]+)/(approve|reject|cancel)", route)
            if match and self.command == "POST":
                return self._send(200, self.service.process_work_request(actor_id, match.group(1), match.group(2), body.get("reason")))

            if route == "/wards/me/audit-logs" and self.command == "GET":
                data, meta = self.service.audit_logs(actor_id, query)
                return self._send(200, data, meta)

            raise ApiError(404, "RESOURCE_NOT_FOUND", "요청 경로를 찾을 수 없습니다.")
        except ApiError as exc:
            self._abandon_idempotency()
            return self._send_error(exc.status, exc.code, exc.message, trace_id, exc.field_errors)
        except json.JSONDecodeError:
            self._abandon_idempotency()
            return self._send_error(400, "INVALID_JSON", "JSON 본문을 해석할 수 없습니다.", trace_id)
        except (TypeError, ValueError) as exc:
            self._abandon_idempotency()
            return self._send_error(400, "VALIDATION_ERROR", str(exc), trace_id)
        except Exception:
            self._abandon_idempotency()
            traceback.print_exc()
            return self._send_error(500, "INTERNAL_ERROR", "서버 오류가 발생했습니다.", trace_id)

    def _request_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        if length > self.server.max_request_bytes:
            raise ApiError(413, "REQUEST_TOO_LARGE", "요청 본문 크기 제한을 초과했습니다.")
        content_type = self.headers.get("Content-Type", "")
        media_type = content_type.split(";", 1)[0].strip().lower()
        raw = self.rfile.read(length)
        if media_type == "application/json":
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ApiError(400, "INVALID_JSON", "요청 본문은 JSON 객체여야 합니다.")
            return value
        if media_type == "multipart/form-data":
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + raw
            )
            result = {}
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if not name:
                    continue
                filename = part.get_filename()
                payload = part.get_payload(decode=True) or b""
                if filename is not None:
                    result[name] = payload
                    result["filename"] = filename
                else:
                    result[name] = payload.decode(part.get_content_charset() or "utf-8")
            return result
        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type은 application/json 또는 multipart/form-data여야 합니다.")

    def _identity(self):
        cookie_header = self.headers.get("Cookie", "")
        if cookie_header:
            cookie = SimpleCookie()
            cookie.load(cookie_header)
            morsel = cookie.get("ward_session")
            if morsel:
                session = self.service.session_user(morsel.value)
                return {**session, "sessionId": morsel.value}
        if self.server.trust_proxy_headers and self.headers.get("X-User-Id"):
            return {"userId": self.headers["X-User-Id"]}
        if self.server.dev_user_id:
            return {"userId": self.server.dev_user_id}
        return {}

    def _send(self, status, data, meta=None, raw=False, extra_headers=None):
        payload = data if raw else {"data": data}
        if meta is not None:
            payload["meta"] = meta
        if 200 <= status < 300 and self._idempotency_record_id:
            self.service.complete_idempotency(self._idempotency_record_id, status, payload)
            self._idempotency_record_id = None
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        headers = extra_headers.items() if isinstance(extra_headers, dict) else (extra_headers or [])
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    def _send_empty(self, status, extra_headers=None):
        if 200 <= status < 300 and self._idempotency_record_id:
            self.service.complete_idempotency(self._idempotency_record_id, status, None)
            self._idempotency_record_id = None
        self.send_response(status)
        self._common_headers()
        headers = extra_headers.items() if isinstance(extra_headers, dict) else (extra_headers or [])
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()

    def _send_error(self, status, code, message, trace_id, field_errors=None):
        payload = {
            "error": {
                "code": code,
                "message": message,
                "traceId": trace_id,
                "fieldErrors": field_errors or [],
            }
        }
        self._send(status, payload, raw=True)

    def _common_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if self.server.production:
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if getattr(self, "_current_trace_id", None):
            self.send_header("X-Request-Id", self._current_trace_id)
        origin = self.headers.get("Origin")
        if origin and origin in self.server.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")

    def _send_binary(self, status, content, content_type, filename):
        self.send_response(status)
        self._common_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _abandon_idempotency(self):
        if self._idempotency_record_id:
            self.service.abandon_idempotency(self._idempotency_record_id)
            self._idempotency_record_id = None


class WardHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, service, dev_user_id=None, trust_proxy_headers=False, secure_cookies=False,
                 allowed_origins=None, production=False, json_logs=False, max_request_bytes=11 * 1024 * 1024):
        super().__init__(address, ApiHandler)
        self.service = service
        self.dev_user_id = dev_user_id
        self.trust_proxy_headers = trust_proxy_headers
        self.secure_cookies = secure_cookies
        self.allowed_origins = allowed_origins or set()
        self.production = production
        self.json_logs = json_logs
        self.max_request_bytes = max_request_bytes
        self._login_attempts = defaultdict(deque)
        self._login_lock = threading.Lock()

    def allow_login(self, client_ip):
        now = time.monotonic()
        with self._login_lock:
            attempts = self._login_attempts[client_ip]
            while attempts and now - attempts[0] > 60:
                attempts.popleft()
            if len(attempts) >= 20:
                return False
            attempts.append(now)
            return True


def _env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def create_server(host="127.0.0.1", port=8000, db_path=None, seed_demo=True):
    production = os.getenv("APP_ENV", "development").strip().lower() == "production"
    if production:
        seed_demo = False
    db_path = db_path or os.getenv("WARD_DB_PATH", "./var/ward_scheduler.db")
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db = Database(db_path)
    db.initialize()
    seed = db.seed_demo() if seed_demo else {}
    configured_dev_user = os.getenv("WARD_DEV_USER_ID")
    if production and configured_dev_user:
        raise RuntimeError("운영 환경에서는 WARD_DEV_USER_ID를 사용할 수 없습니다.")
    dev_user = None if production else configured_dev_user or seed.get("userId")
    trust_proxy = _env_flag("WARD_TRUST_PROXY_HEADERS")
    secure_cookies = _env_flag("WARD_SECURE_COOKIES", production)
    default_origins = "" if production else "http://localhost:3000,http://127.0.0.1:3000"
    allowed_origins = {item.strip() for item in os.getenv("WARD_ALLOWED_ORIGINS", default_origins).split(",") if item.strip()}
    if production and not allowed_origins:
        raise RuntimeError("운영 환경에서는 WARD_ALLOWED_ORIGINS를 설정해야 합니다.")
    json_logs = _env_flag("WARD_JSON_LOGS", production)
    async_generation = _env_flag("WARD_ASYNC_GENERATION", production)
    max_request_bytes = int(os.getenv("WARD_MAX_REQUEST_BYTES", str(11 * 1024 * 1024)))
    return WardHttpServer((host, port), FeatureWardService(db, async_generation=async_generation), dev_user, trust_proxy, secure_cookies,
                          allowed_origins, production, json_logs, max_request_bytes)


def main():
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    server = create_server(host, port, seed_demo=_env_flag("WARD_SEED_DEMO", True))
    print(f"Ward Scheduler API: http://{host}:{server.server_port}")
    print(f"Demo actor: {server.dev_user_id}")
    def stop_server(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
