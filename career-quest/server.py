"""Zero-dependency local web server for the Career Quest hackathon MVP."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import socket
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from engine import CareerEngine
from coaching import coaching
from auth import AuthError, AuthStore
from ai_recommender import AIRecommender, enabled as ai_enabled
from upload_parser import _bundle_from_uploaded_files, _strict_json_file


ROOT = Path(__file__).resolve().parent
ENGINE = CareerEngine()
AUTH = None
AI_RECOMMENDER = AIRecommender()


class CareerQuestServer(ThreadingHTTPServer):
    # Windows otherwise allows two servers to bind the same port, sending
    # login requests to a different process than the one that printed passwords.
    allow_reuse_address = False

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def configure_auth():
    global AUTH
    accounts = []
    for username, role, employee_id, variable in [
        ("employee", "employee", os.getenv("CQ_EMPLOYEE_ID", "E0028"), "CQ_EMPLOYEE_PASSWORD"),
        ("hr", "hr", None, "CQ_HR_PASSWORD"),
    ]:
        password = os.getenv(variable) or secrets.token_urlsafe(12)
        accounts.append((username, password, role, employee_id))
        if not os.getenv(variable):
            print(f"Local login: {username} / {password}")
    ENGINE.employee(accounts[0][3])
    AUTH = AuthStore(accounts)


class CareerQuestHandler(BaseHTTPRequestHandler):
    server_version = "CareerQuest/1.0"

    def end_headers(self):
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        super().end_headers()

    def _json(self, payload: object, status: int = 200, cookie: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if status == 429:
            self.send_header("Retry-After", "60")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Ожидается application/json")
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 8_000_000:
            raise ValueError("Файл слишком большой для демо-режима")
        raw = self.rfile.read(length)
        payload = _strict_json_file(raw.decode("utf-8-sig"), "request") if raw else {}
        if not isinstance(payload, dict):
            raise ValueError("Ожидается JSON-объект")
        return payload

    def _token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            return cookie["cq_session"].value if "cq_session" in cookie else ""
        except Exception:
            return ""

    def _session(self):
        if AUTH is None:
            raise AuthError("Перезапустите сервер для включения входа", 503)
        return AUTH.session(self._token())

    @staticmethod
    def _cookie(token, age=28800):
        secure = "; Secure" if os.getenv("CQ_SECURE_COOKIE") == "1" else ""
        return f"cq_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}{secure}"

    @staticmethod
    def _role(session, role):
        if session["user"]["role"] != role:
            raise AuthError("Недостаточно прав", 403)

    def _employee_id(self, session, requested=None):
        user = session["user"]
        employee_id = requested or user["employee_id"]
        if user["role"] == "employee" and employee_id != user["employee_id"]:
            raise AuthError("Доступен только ваш профиль", 403)
        return employee_id

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/health":
                return self._json({"status": "ok"})
            if path.startswith("/api/"):
                session = self._session()
            if path == "/api/session":
                return self._json({"user": session["user"], "csrf": session["csrf"], "expires": session["expires"]})
            if path == "/api/employees":
                self._role(session, "hr")
                employees = [
                    {
                        "employee_id": item["employee_id"],
                        "name": item.get("name", item["employee_id"]),
                        "role": item.get("role", ""),
                        "role_label": __import__("engine").ROLE_LABELS.get(item.get("role"), item.get("role", "")),
                        "grade": item.get("grade", ""),
                    }
                    for item in ENGINE.employees
                ]
                return self._json({"employees": employees, "data_source": ENGINE.data_source})
            if path == "/api/employee":
                employee_id = self._employee_id(session, parse_qs(parsed.query).get("id", [None])[0])
                if session["user"]["role"] == "hr":
                    AUTH.audit("profile_viewed", session["user"]["username"], "hr", "allowed", employee_id)
                profile = ENGINE.employee_view(employee_id)
                profile["coaching"] = coaching(profile)
                profile["ai_available"] = ai_enabled()
                return self._json(profile)
            if path == "/api/hr":
                self._role(session, "hr")
                return self._json(ENGINE.hr_view())
            if path == "/api/security":
                self._role(session, "hr")
                return self._json(AUTH.security_summary())
            if path == "/api/planner":
                query = parse_qs(parsed.query)
                employee_id = self._employee_id(session, query.get("id", [None])[0])
                return self._json(ENGINE.planner(employee_id, query.get("hours", [8])[0]))
            if path.startswith("/api/"):
                return self._json({"error": "Маршрут не найден"}, 404)
            return self._serve_static(path)
        except AuthError as exc:
            self._json({"error": str(exc)}, exc.status)
        except KeyError as exc:
            self._json({"error": str(exc)}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:  # keep demo responsive and return readable diagnostics
            self._json({"error": str(exc)}, 500)

    def do_POST(self) -> None:  # noqa: N802
        try:
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{self.headers.get('Host')}", f"https://{self.headers.get('Host')}"}:
                raise AuthError("Запрос с другого сайта запрещён", 403)
            payload = self._body()
            if self.path == "/api/login":
                if AUTH is None:
                    raise AuthError("Вход не настроен", 503)
                username, password = payload.get("username", ""), payload.get("password", "")
                if not isinstance(username, str) or not isinstance(password, str) or len(password) > 1024:
                    raise ValueError("Некорректные данные входа")
                token, session = AUTH.login(username, password, self.client_address[0])
                AUTH.logout(self._token())
                return self._json({"user": session["user"], "csrf": session["csrf"], "expires": session["expires"]}, cookie=self._cookie(token))
            session = self._session()
            if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"]):
                raise AuthError("Обновите страницу и повторите запрос", 403)
            if self.path == "/api/logout":
                AUTH.logout(self._token())
                return self._json({"status": "ok"}, cookie=self._cookie("", 0))
            if self.path == "/api/recommendations/ai":
                employee_id = self._employee_id(session, payload.get("employee_id"))
                return self._json(AI_RECOMMENDER.rerank(ENGINE, employee_id, payload.get("hours")))
            if self.path == "/api/simulate":
                employee_id = self._employee_id(session, payload.get("employee_id"))
                return self._json(ENGINE.simulate(employee_id, payload.get("event_ids"), payload.get("hours", 8)))
            if self.path in {"/api/coach", "/api/complete", "/api/participation"}:
                self._role(session, "employee")
                employee_id = self._employee_id(session, payload.get("employee_id"))
            if self.path == "/api/participation":
                return self._json(ENGINE.set_participation(employee_id, payload.get("paused")))
            if self.path == "/api/coach":
                return self._json(coaching(ENGINE.employee_view(employee_id), generate=True))
            if self.path == "/api/complete":
                result = ENGINE.complete(employee_id, str(payload["event_id"]))
                AI_RECOMMENDER.invalidate()
                result["coaching"] = coaching(result)
                result["ai_available"] = ai_enabled()
                return self._json(result)
            if self.path in {"/api/upload", "/api/upload/preview"}:
                self._role(session, "hr")
                bundle = _bundle_from_uploaded_files(payload["files"]) if "files" in payload else payload
                preview = self.path.endswith("/preview")
                with ENGINE.lock:
                    if "revision" in payload and payload["revision"] != ENGINE.revision:
                        raise ValueError("Данные изменились. Повторите предпросмотр импорта.")
                    result = ENGINE.upload_bundle(bundle, mode=payload.get("mode", "legacy"), preview=preview)
                if not preview:
                    AI_RECOMMENDER.invalidate()
                return self._json(result)
            if self.path == "/api/reset":
                self._role(session, "hr")
                ENGINE.reset()
                AI_RECOMMENDER.invalidate()
                return self._json({"status": "ok", "data_source": ENGINE.data_source})
            self._json({"error": "Маршрут не найден"}, 404)
        except AuthError as exc:
            self._json({"error": str(exc)}, exc.status)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else unquote(path.lstrip("/"))
        if relative not in {"index.html", "app.js", "styles.css"}:
            return self._json({"error": "Файл не найден"}, 404)
        file_path = (ROOT / relative).resolve()
        if ROOT not in file_path.parents and file_path != ROOT:
            return self._json({"error": "Недопустимый путь"}, 403)
        if not file_path.is_file():
            file_path = ROOT / "index.html"
        content = file_path.read_bytes()
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def do_HEAD(self):
        self._serve_static(urlparse(self.path).path)

    def version_string(self):
        return "A^2SCEND"

    def log_message(self, format_string: str, *args: object) -> None:
        if os.getenv("CQ_QUIET") != "1":
            super().log_message(format_string, *args)


def main() -> None:
    global ENGINE
    parser = argparse.ArgumentParser(description="A^2SCEND local server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    args = parser.parse_args()
    if os.getenv("CQ_STORAGE_PATH"):
        ENGINE = CareerEngine(os.environ["CQ_STORAGE_PATH"])
    try:
        server = CareerQuestServer((args.host, args.port), CareerQuestHandler)
    except OSError as exc:
        parser.exit(1, f"Не удалось запустить сервер на {args.host}:{args.port}. Остановите предыдущий сервер или выберите другой --port. ({exc})\n")
    configure_auth()
    print(f"A^2SCEND: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
