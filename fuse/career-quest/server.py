"""Hardened zero-dependency web server for the Career Quest hackathon MVP.

The authentication implemented here is intentionally a local demo adapter.  The
authorization policy, however, is real and enforced server-side so the adapter
can later be replaced with Halyk OIDC/SSO without changing endpoint rules.
"""

from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import socket
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from engine import CareerEngine, ROLE_LABELS, parse_csv_text
from security import AuthManager, Session


ROOT = Path(__file__).resolve().parent
ENGINE = CareerEngine()
ENGINE_LOCK = threading.RLock()
AUTH = AuthManager()
MAX_BODY_BYTES = 8_000_000
MAX_UPLOAD_FILES = 8
MAX_UPLOAD_FILE_BYTES = 4_000_000
MAX_UPLOAD_CONTENT_BYTES = 6_000_000
MAX_UPLOAD_FILENAME_LENGTH = 180
UPLOAD_SECTIONS = {"employees", "profiles", "events", "skills", "history", "activity_history"}
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
}


class RequestError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Повторяющееся поле: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"Недопустимое числовое значение: {value}")


def _guard_json_tree(value: Any, *, max_nodes: int = 100_000, max_depth: int = 32) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise ValueError("JSON содержит слишком много элементов")
        if depth > max_depth:
            raise ValueError("JSON имеет слишком большую глубину")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def _strict_json_file(text: str, filename: str) -> Any:
    try:
        value = json.loads(
            text.lstrip("\ufeff"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        _guard_json_tree(value)
        return value
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValueError(f"{filename}: некорректный JSON ({exc})") from exc


def _canonical_section(section: str) -> str:
    if section in {"employees", "profiles"}:
        return "employees"
    if section in {"history", "activity_history"}:
        return "history"
    return section


def _infer_json_section(value: Any, filename: str) -> tuple[str, Any]:
    lower_name = filename.lower()
    name_hints = {
        "employee": "employees",
        "profile": "employees",
        "event": "events",
        "skill": "skills",
        "history": "history",
    }
    hinted_sections = {section for marker, section in name_hints.items() if marker in lower_name}
    if len(hinted_sections) > 1:
        raise ValueError(f"{filename}: имя файла неоднозначно")
    if hinted_sections:
        section = hinted_sections.pop()
        if isinstance(value, dict):
            if section != "skills" or "skill_id" in value:
                value = [value]
        return section, value

    records = value if isinstance(value, list) else [value]
    if not records or not all(isinstance(item, dict) for item in records):
        raise ValueError(f"Не удалось однозначно определить тип файла {filename}")

    is_history = all("employee_id" in item and "event_id" in item and "status" in item for item in records)
    candidates = []
    if is_history:
        candidates.append("history")
    elif all("employee_id" in item for item in records):
        candidates.append("employees")
    if all("event_id" in item and "employee_id" not in item for item in records):
        candidates.append("events")
    if all("skill_id" in item for item in records):
        candidates.append("skills")
    if len(candidates) != 1:
        raise ValueError(f"Не удалось однозначно определить тип файла {filename}")
    section = candidates[0]
    if isinstance(value, dict) and section != "skills":
        value = [value]
    return section, value


def _bundle_from_uploaded_files(files: Any) -> dict[str, Any]:
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_UPLOAD_FILES:
        raise ValueError(f"Можно загрузить от 1 до {MAX_UPLOAD_FILES} файлов")

    bundle: dict[str, Any] = {}
    total_bytes = 0

    def add_section(section: str, value: Any, filename: str) -> None:
        canonical = _canonical_section(section)
        if canonical in bundle:
            raise ValueError(f"{filename}: раздел {canonical} уже загружен другим файлом")
        bundle[canonical] = value

    for index, descriptor in enumerate(files):
        if not isinstance(descriptor, dict) or set(descriptor) != {"name", "content"}:
            raise ValueError(f"files[{index}] должен содержать только name и content")
        filename = descriptor["name"]
        content = descriptor["content"]
        if (
            not isinstance(filename, str)
            or not filename.strip()
            or len(filename) > MAX_UPLOAD_FILENAME_LENGTH
            or "/" in filename
            or "\\" in filename
            or "\x00" in filename
        ):
            raise ValueError(f"files[{index}].name недопустимо")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"{filename}: файл пуст или не является текстовым")
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > MAX_UPLOAD_FILE_BYTES:
            raise ValueError(f"{filename}: файл превышает {MAX_UPLOAD_FILE_BYTES // 1_000_000} МБ")
        total_bytes += content_bytes
        if total_bytes > MAX_UPLOAD_CONTENT_BYTES:
            raise ValueError("Суммарный размер файлов превышает 6 МБ")

        lower_name = filename.lower()
        if lower_name.endswith(".csv"):
            add_section("history", parse_csv_text(content), filename)
            continue
        if not lower_name.endswith(".json"):
            raise ValueError(f"{filename}: поддерживаются только JSON и CSV")

        value = _strict_json_file(content, filename)
        is_record = isinstance(value, dict) and any(
            identifier in value for identifier in ("employee_id", "event_id", "skill_id")
        )
        if isinstance(value, dict) and not is_record and set(value).intersection(UPLOAD_SECTIONS):
            unknown = set(value) - UPLOAD_SECTIONS
            if unknown:
                raise ValueError(f"{filename}: неизвестные разделы: {', '.join(sorted(unknown))}")
            for section, section_value in value.items():
                add_section(section, section_value, filename)
            continue
        section, section_value = _infer_json_section(value, filename)
        add_section(section, section_value, filename)
    return bundle


class HardenedThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def get_request(self) -> tuple[socket.socket, Any]:
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection, address


class CareerQuestHandler(BaseHTTPRequestHandler):
    server_version = "CareerQuest"
    sys_version = ""

    def version_string(self) -> str:
        return "CareerQuest"

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        if self._is_https():
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        super().end_headers()

    def _is_https(self) -> bool:
        if os.getenv("CQ_COOKIE_SECURE") == "1":
            return True
        return os.getenv("CQ_TRUST_PROXY") == "1" and self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def _client_id(self) -> str:
        if os.getenv("CQ_TRUST_PROXY") == "1":
            forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
            if forwarded:
                return forwarded[:80]
        return str(self.client_address[0])[:80]

    def _json(
        self,
        payload: object,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _body(self, limit: int = MAX_BODY_BYTES) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise RequestError(415, "Требуется Content-Type: application/json")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise RequestError(411, "Не указан размер запроса")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise RequestError(400, "Некорректный размер запроса") from exc
        if length < 0:
            raise RequestError(400, "Некорректный размер запроса")
        if length > limit:
            raise RequestError(413, "Запрос превышает допустимый размер")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise RequestError(400, "Тело запроса получено не полностью")
        try:
            payload = json.loads(
                raw.decode("utf-8") if raw else "{}",
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise RequestError(400, "Некорректный JSON") from exc
        if not isinstance(payload, dict):
            raise RequestError(400, "Корневой элемент JSON должен быть объектом")
        try:
            _guard_json_tree(payload)
        except ValueError as exc:
            raise RequestError(400, str(exc)) from exc
        return payload

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        host = self.headers.get("Host", "").lower()
        return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host

    def _cookie_token(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        try:
            cookies = SimpleCookie(raw)
        except Exception:
            return None
        morsel = cookies.get("cq_session")
        return morsel.value if morsel else None

    def _session(self, roles: set[str] | None = None) -> Session | None:
        session = AUTH.session(self._cookie_token())
        if not session:
            self._json({"error": "Требуется вход в систему", "code": "AUTH_REQUIRED"}, 401)
            return None
        if roles is not None and session.role not in roles:
            AUTH.audit("access_denied", session.username, session.role, "denied", f"{self.command} {urlparse(self.path).path}")
            self._json({"error": "Недостаточно прав для этого действия", "code": "FORBIDDEN"}, 403)
            return None
        return session

    def _csrf_valid(self, session: Session) -> bool:
        supplied = self.headers.get("X-CSRF-Token", "")
        return bool(supplied) and hmac.compare_digest(supplied, session.csrf_token)

    def _session_cookie(self, token: str, max_age: int) -> str:
        parts = [f"cq_session={token}", "Path=/", f"Max-Age={max_age}", "HttpOnly", "SameSite=Strict"]
        if self._is_https():
            parts.append("Secure")
        return "; ".join(parts)

    @staticmethod
    def _employee_summary(item: dict[str, Any]) -> dict[str, Any]:
        role = str(item.get("role", ""))[:80]
        return {
            "employee_id": str(item.get("employee_id", ""))[:80],
            "name": str(item.get("name") or item.get("employee_id", ""))[:160],
            "role": role,
            "role_label": ROLE_LABELS.get(role, role),
            "grade": str(item.get("grade", ""))[:40],
        }

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/health":
                return self._json({"status": "ok", "service": "career-quest"})
            if path == "/api/config":
                return self._json(
                    {
                        "demo_mode": AUTH.demo_mode,
                        "demo_quick_login": AUTH.quick_login,
                        "auth": "session-cookie",
                        "planned_identity": "OIDC/SSO",
                    }
                )
            if path == "/api/me":
                session = self._session()
                if session:
                    return self._json({"user": session.public()})
                return None
            if path == "/api/employees":
                session = self._session()
                if not session:
                    return None
                with ENGINE_LOCK:
                    if session.role == "employee":
                        employees = [self._employee_summary(ENGINE.employee(str(session.employee_id)))]
                    else:
                        employees = [self._employee_summary(item) for item in ENGINE.employees]
                    data_source = ENGINE.data_source
                return self._json({"employees": employees, "data_source": data_source})
            if path == "/api/employee":
                session = self._session()
                if not session:
                    return None
                requested = parse_qs(parsed.query).get("id", [session.employee_id or ""])[0]
                if session.role == "employee" and requested != session.employee_id:
                    AUTH.audit("idor_blocked", session.username, session.role, "denied", "Attempted access to another employee")
                    return self._json({"error": "Доступ к этому профилю запрещён", "code": "OBJECT_FORBIDDEN"}, 403)
                with ENGINE_LOCK:
                    result = ENGINE.employee_view(str(requested))
                if session.role == "hr":
                    AUTH.audit("profile_viewed", session.username, session.role, "allowed", str(requested)[:80])
                return self._json(result)
            if path == "/api/hr":
                session = self._session({"hr"})
                if not session:
                    return None
                with ENGINE_LOCK:
                    result = ENGINE.hr_view()
                AUTH.audit("hr_dashboard_viewed", session.username, session.role, "allowed", "Aggregated team analytics")
                return self._json(result)
            if path == "/api/security":
                session = self._session({"hr"})
                if not session:
                    return None
                return self._json(AUTH.security_summary())
            if path.startswith("/api/"):
                return self._json({"error": "Маршрут API не найден", "code": "NOT_FOUND"}, 404)
            return self._serve_static(path)
        except KeyError:
            self._json({"error": "Объект не найден", "code": "NOT_FOUND"}, 404)
        except Exception:
            AUTH.audit("server_error", "system", "system", "error", f"GET {path}")
            self._json({"error": "Внутренняя ошибка запроса", "code": "INTERNAL_ERROR"}, 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if not self._origin_allowed():
                return self._json({"error": "Запрос с другого источника отклонён", "code": "ORIGIN_REJECTED"}, 403)

            if path == "/api/login":
                payload = self._body(16_384)
                username = payload.get("username")
                password = payload.get("password")
                if not isinstance(username, str) or not isinstance(password, str):
                    return self._json({"error": "Неверный логин или пароль", "code": "INVALID_CREDENTIALS"}, 401)
                session, outcome, retry_after = AUTH.authenticate(username, password, self._client_id())
                if not session:
                    if outcome == "rate_limited":
                        return self._json(
                            {"error": "Слишком много попыток. Повторите позже", "code": "RATE_LIMITED"},
                            429,
                            {"Retry-After": str(retry_after)},
                        )
                    return self._json({"error": "Неверный логин или пароль", "code": "INVALID_CREDENTIALS"}, 401)
                return self._json(
                    {"user": session.public()},
                    200,
                    {"Set-Cookie": self._session_cookie(session.token, int(session.expires_at - session.created_at))},
                )

            session = self._session()
            if not session:
                return None
            if not self._csrf_valid(session):
                AUTH.audit("csrf_blocked", session.username, session.role, "denied", f"POST {path}")
                return self._json({"error": "Защитный токен недействителен", "code": "CSRF_REJECTED"}, 403)

            if path == "/api/logout":
                AUTH.logout(self._cookie_token())
                return self._json(
                    {"status": "ok"},
                    200,
                    {"Set-Cookie": self._session_cookie("", 0)},
                )

            if path == "/api/complete":
                if session.role != "employee":
                    AUTH.audit("completion_denied", session.username, session.role, "denied", "Only employee can complete own activity")
                    return self._json({"error": "Только сотрудник может подтвердить свою активность", "code": "FORBIDDEN"}, 403)
                payload = self._body(65_536)
                supplied_employee = payload.get("employee_id")
                if supplied_employee is not None and supplied_employee != session.employee_id:
                    AUTH.audit("idor_blocked", session.username, session.role, "denied", "Attempted mutation of another employee")
                    return self._json({"error": "Нельзя изменить прогресс другого сотрудника", "code": "OBJECT_FORBIDDEN"}, 403)
                event_id = payload.get("event_id")
                if not isinstance(event_id, str) or not event_id:
                    raise RequestError(422, "Не указан event_id")
                with ENGINE_LOCK:
                    result = ENGINE.complete(str(session.employee_id), event_id[:100])
                AUTH.audit("activity_completed", session.username, session.role, "allowed", event_id[:80])
                return self._json(result)

            if path == "/api/upload":
                if session.role != "hr":
                    AUTH.audit("upload_denied", session.username, session.role, "denied", "Role mismatch")
                    return self._json({"error": "Загрузка доступна только HR", "code": "FORBIDDEN"}, 403)
                payload = self._body(MAX_BODY_BYTES)
                if "files" in payload:
                    if set(payload) != {"files"}:
                        raise ValueError("Файлы нельзя смешивать с готовыми разделами набора")
                    payload = _bundle_from_uploaded_files(payload["files"])
                with ENGINE_LOCK:
                    result = ENGINE.upload_bundle_validated(payload)
                AUTH.audit("dataset_uploaded", session.username, session.role, "allowed", "Validated dataset committed")
                return self._json(result)

            if path == "/api/reset":
                if session.role != "hr":
                    AUTH.audit("reset_denied", session.username, session.role, "denied", "Role mismatch")
                    return self._json({"error": "Сброс доступен только HR", "code": "FORBIDDEN"}, 403)
                self._body(2_048)
                with ENGINE_LOCK:
                    ENGINE.reset()
                AUTH.audit("dataset_reset", session.username, session.role, "allowed", "Demo dataset restored")
                return self._json({"status": "ok", "data_source": ENGINE.data_source})

            return self._json({"error": "Маршрут API не найден", "code": "NOT_FOUND"}, 404)
        except RequestError as exc:
            self._json({"error": exc.message, "code": "INVALID_REQUEST"}, exc.status)
        except KeyError:
            self._json({"error": "Объект не найден", "code": "NOT_FOUND"}, 404)
        except ValueError as exc:
            self._json({"error": str(exc), "code": "VALIDATION_ERROR"}, 422)
        except Exception:
            AUTH.audit("server_error", "system", "system", "error", f"POST {path}")
            self._json({"error": "Внутренняя ошибка запроса", "code": "INTERNAL_ERROR"}, 500)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Allow", "GET, POST, HEAD, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def _method_not_allowed(self) -> None:
        self._json({"error": "Метод не поддерживается", "code": "METHOD_NOT_ALLOWED"}, 405, {"Allow": "GET, POST, HEAD, OPTIONS"})

    def _serve_static(self, path: str) -> None:
        normalized = unquote(path)
        relative = STATIC_FILES.get(normalized)
        if not relative:
            return self._json({"error": "Файл не найден", "code": "NOT_FOUND"}, 404)
        file_path = ROOT / relative
        content = file_path.read_bytes()
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        # Revalidate every demo asset so a judge never receives mismatched HTML
        # and JavaScript after a last-minute hackathon update.
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def log_message(self, format_string: str, *args: object) -> None:
        if os.getenv("CQ_QUIET") != "1":
            super().log_message(format_string, *args)


def build_server(host: str = "127.0.0.1", port: int = 0) -> HardenedThreadingHTTPServer:
    return HardenedThreadingHTTPServer((host, port), CareerQuestHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Career Quest secure local server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    args = parser.parse_args()
    server = build_server(args.host, args.port)
    print(f"Career Quest: http://{args.host}:{server.server_port}")
    if AUTH.quick_login:
        print("Demo employee: employee / EmployeeDemo!2026")
        print("Demo HR:       hr / HRDemo!2026#")
    elif AUTH.demo_mode:
        print("Demo accounts use passwords configured in environment variables.")
    print("Production: set CQ_DEMO_MODE=0 and passwords via environment; place behind HTTPS.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
