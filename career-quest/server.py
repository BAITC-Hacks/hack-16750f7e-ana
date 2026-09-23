"""Zero-dependency local web server for the Career Quest hackathon MVP."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from engine import CareerEngine


ROOT = Path(__file__).resolve().parent
ENGINE = CareerEngine()


class CareerQuestHandler(BaseHTTPRequestHandler):
    server_version = "CareerQuest/1.0"

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 8_000_000:
            raise ValueError("Файл слишком большой для демо-режима")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/health":
                return self._json({"status": "ok"})
            if path == "/api/employees":
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
                employee_id = parse_qs(parsed.query).get("id", ["E0028"])[0]
                return self._json(ENGINE.employee_view(employee_id))
            if path == "/api/hr":
                return self._json(ENGINE.hr_view())
            return self._serve_static(path)
        except KeyError as exc:
            self._json({"error": str(exc)}, 404)
        except Exception as exc:  # keep demo responsive and return readable diagnostics
            self._json({"error": str(exc)}, 500)

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = self._body()
            if self.path == "/api/complete":
                result = ENGINE.complete(str(payload["employee_id"]), str(payload["event_id"]))
                return self._json(result)
            if self.path == "/api/upload":
                return self._json(ENGINE.upload_bundle(payload))
            if self.path == "/api/reset":
                ENGINE.reset()
                return self._json({"status": "ok", "data_source": ENGINE.data_source})
            self._json({"error": "Маршрут не найден"}, 404)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else unquote(path.lstrip("/"))
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
        self.wfile.write(content)

    def log_message(self, format_string: str, *args: object) -> None:
        if os.getenv("CQ_QUIET") != "1":
            super().log_message(format_string, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Career Quest local server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), CareerQuestHandler)
    print(f"Career Quest: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
