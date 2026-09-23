"""End-to-end security contract for the local Career Quest server."""

from __future__ import annotations

import http.cookiejar
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["CQ_QUIET"] = "1"

import server  # noqa: E402


class SecurityIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = server.build_server("127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        server.AUTH.reset_runtime()
        with server.ENGINE_LOCK:
            server.ENGINE.reset()
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: object | None = None,
        headers: dict[str, str] | None = None,
        opener: urllib.request.OpenerDirector | None = None,
        raw: bytes | None = None,
    ) -> tuple[int, dict, object]:
        request_headers = dict(headers or {})
        body = raw
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        if body is not None and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=body, method=method, headers=request_headers)
        client = opener or self.opener
        try:
            response = client.open(request, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        data = response.read()
        response_headers = response.headers
        response.close()
        parsed = json.loads(data.decode("utf-8")) if data else {}
        return response.status, parsed, response_headers

    def login(self, username: str, password: str, opener: urllib.request.OpenerDirector | None = None) -> tuple[str, object]:
        status, data, headers = self.request(
            "/api/login",
            method="POST",
            payload={"username": username, "password": password},
            opener=opener,
        )
        self.assertEqual(status, 200, data)
        self.assertNotIn("token", data["user"])
        return data["user"]["csrf_token"], headers

    def test_anonymous_only_gets_public_surface(self) -> None:
        status, data, _ = self.request("/api/health")
        self.assertEqual((status, data["status"]), (200, "ok"))
        for path in ("/api/me", "/api/employees", "/api/employee?id=E0028", "/api/hr", "/api/security"):
            with self.subTest(path=path):
                status, data, _ = self.request(path)
                self.assertEqual(status, 401)
                self.assertEqual(data["code"], "AUTH_REQUIRED")
        status, _, _ = self.request("/api/complete", method="POST", payload={"event_id": "EV_SYSTEM_DESIGN"})
        self.assertEqual(status, 401)

    def test_employee_is_bound_to_own_identity(self) -> None:
        csrf, _ = self.login("employee", "EmployeeDemo!2026")
        status, directory, _ = self.request("/api/employees")
        self.assertEqual(status, 200)
        self.assertEqual([item["employee_id"] for item in directory["employees"]], ["E0028"])
        status, profile, _ = self.request("/api/employee?id=E0028")
        self.assertEqual(status, 200)
        self.assertEqual(profile["employee"]["employee_id"], "E0028")

        for path in ("/api/employee?id=E0001", "/api/hr", "/api/security"):
            with self.subTest(path=path):
                status, data, _ = self.request(path)
                self.assertEqual(status, 403)
                self.assertNotIn("E0001", json.dumps(data))

        status, _, _ = self.request(
            "/api/complete",
            method="POST",
            payload={"employee_id": "E0001", "event_id": "EV_SYSTEM_DESIGN"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 403)
        for path in ("/api/upload", "/api/reset"):
            status, _, _ = self.request(path, method="POST", payload={}, headers={"X-CSRF-Token": csrf})
            self.assertEqual(status, 403)

    def test_csrf_origin_and_valid_completion(self) -> None:
        csrf, _ = self.login("employee", "EmployeeDemo!2026")
        status, profile, _ = self.request("/api/employee?id=E0028")
        event_id = profile["recommendations"][0]["event_id"]
        before_history = len(profile["history"])

        status, data, _ = self.request("/api/complete", method="POST", payload={"event_id": event_id})
        self.assertEqual((status, data["code"]), (403, "CSRF_REJECTED"))
        status, data, _ = self.request(
            "/api/complete",
            method="POST",
            payload={"event_id": event_id},
            headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
        )
        self.assertEqual((status, data["code"]), (403, "ORIGIN_REJECTED"))

        status, updated, _ = self.request(
            "/api/complete",
            method="POST",
            payload={"event_id": event_id},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(updated["history"]), before_history + 1)
        status, data, _ = self.request(
            "/api/complete",
            method="POST",
            payload={"event_id": event_id},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual((status, data["code"]), (422, "VALIDATION_ERROR"))

    def test_hr_scope_is_read_only_for_employee_progress(self) -> None:
        csrf, _ = self.login("hr", "HRDemo!2026#")
        status, directory, _ = self.request("/api/employees")
        self.assertEqual(status, 200)
        self.assertGreater(len(directory["employees"]), 100)
        self.assertEqual(self.request("/api/hr")[0], 200)
        self.assertEqual(self.request("/api/security")[0], 200)
        status, data, _ = self.request(
            "/api/complete",
            method="POST",
            payload={"employee_id": "E0028", "event_id": "EV_SYSTEM_DESIGN"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual((status, data["code"]), (403, "FORBIDDEN"))

    def test_cookie_logout_and_security_headers(self) -> None:
        csrf, login_headers = self.login("employee", "EmployeeDemo!2026")
        cookie = login_headers.get("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn("employee", cookie)
        status, _, headers = self.request("/api/me")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertIn("default-src 'self'", headers.get("Content-Security-Policy"))
        self.assertNotIn("Python", headers.get("Server", ""))

        status, _, headers = self.request("/api/logout", method="POST", payload={}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", headers.get("Set-Cookie"))
        self.assertEqual(self.request("/api/me")[0], 401)

    def test_static_allowlist_and_api_404(self) -> None:
        status, _, headers = self.request("/app.js", method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-cache")
        for path in ("/server.py", "/engine.py", "/security.py", "/README.md", "/sample_upload/employees.json", "/.git/config"):
            with self.subTest(path=path):
                status, _, _ = self.request(path)
                self.assertEqual(status, 404)
        status, data, headers = self.request("/api/does-not-exist")
        self.assertEqual(status, 404)
        self.assertEqual(data["code"], "NOT_FOUND")
        self.assertTrue(headers.get("Content-Type", "").startswith("application/json"))

    def test_uploaded_fields_are_minimized_and_commit_is_atomic(self) -> None:
        csrf, _ = self.login("hr", "HRDemo!2026#")
        valid = {
            "employees": [{
                "employee_id": "JURY01",
                "name": "<img src=x onerror=alert(1)>",
                "role": "Backend Engineer",
                "grade": "Middle",
                "tenure_months": 31,
                "skills": {"SK_PYTHON": 3, "SK_SYSTEM_DESIGN": 1, "SK_PUBLIC_SPEAKING": 2, "SK_COMMUNICATION": 3},
                "salary": "SECRET",
                "auth_role": "admin",
            }]
        }
        status, result, _ = self.request("/api/upload", method="POST", payload=valid, headers={"X-CSRF-Token": csrf})
        self.assertEqual(status, 200, result)
        status, profile, _ = self.request("/api/employee?id=JURY01")
        self.assertEqual(status, 200)
        serialized = json.dumps(profile)
        self.assertNotIn("SECRET", serialized)
        self.assertNotIn("auth_role", serialized)

        before = self.request("/api/hr")[1]
        invalid = {"employees": [{"employee_id": "BAD01", "name": "Bad", "role": "Backend Engineer", "grade": "Middle", "tenure_months": 1, "skills": {"SK_PYTHON": 9}}]}
        status, data, _ = self.request("/api/upload", method="POST", payload=invalid, headers={"X-CSRF-Token": csrf})
        self.assertEqual((status, data["code"]), (422, "VALIDATION_ERROR"))
        after = self.request("/api/hr")[1]
        self.assertEqual(before["employees"], after["employees"])
        self.assertEqual(before["data_source"], after["data_source"])

    def test_raw_upload_rejects_duplicate_json_keys_and_sections(self) -> None:
        csrf, _ = self.login("hr", "HRDemo!2026#")
        duplicate_key = {
            "files": [{
                "name": "employees.json",
                "content": '[{"employee_id":"FIRST","employee_id":"SECOND"}]',
            }]
        }
        status, data, _ = self.request(
            "/api/upload",
            method="POST",
            payload=duplicate_key,
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual((status, data["code"]), (422, "VALIDATION_ERROR"))
        self.assertIn("Повторяющееся поле", data["error"])

        employee = json.dumps([{
            "employee_id": "DUP01",
            "name": "Duplicate section",
            "role": "Backend Engineer",
            "grade": "Middle",
            "tenure_months": 1,
            "skills": {"SK_PYTHON": 2},
        }])
        duplicate_section = {
            "files": [
                {"name": "employees.json", "content": employee},
                {"name": "profiles.json", "content": employee},
            ]
        }
        status, data, _ = self.request(
            "/api/upload",
            method="POST",
            payload=duplicate_section,
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual((status, data["code"]), (422, "VALIDATION_ERROR"))
        self.assertIn("раздел employees уже загружен", data["error"])

        ambiguous = {
            "files": [{
                "name": "data.json",
                "content": '[{"employee_id":"AMB01","skill_id":"SK_PYTHON"}]',
            }]
        }
        status, data, _ = self.request(
            "/api/upload",
            method="POST",
            payload=ambiguous,
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual((status, data["code"]), (422, "VALIDATION_ERROR"))
        self.assertIn("однозначно определить", data["error"])

        too_many = {
            "files": [{"name": f"employee-{index}.json", "content": employee} for index in range(9)]
        }
        status, data, _ = self.request(
            "/api/upload",
            method="POST",
            payload=too_many,
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual((status, data["code"]), (422, "VALIDATION_ERROR"))
        self.assertIn("от 1 до 8 файлов", data["error"])

    def test_raw_sample_files_are_parsed_and_committed_server_side(self) -> None:
        csrf, _ = self.login("hr", "HRDemo!2026#")
        sample_dir = ROOT / "sample_upload"
        payload = {
            "files": [
                {"name": "employees.json", "content": (sample_dir / "employees.json").read_text(encoding="utf-8")},
                {"name": "activity_history.csv", "content": (sample_dir / "activity_history.csv").read_text(encoding="utf-8")},
            ]
        }
        status, result, _ = self.request(
            "/api/upload",
            method="POST",
            payload=payload,
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(result["data_source"], "Загруженный набор жюри")
        status, profile, _ = self.request("/api/employee?id=JURY01")
        self.assertEqual(status, 200, profile)
        self.assertEqual(profile["employee"]["name"], "Тестовый сотрудник")
        self.assertEqual(len(profile["history"]), 3)

    def test_wrong_content_type_and_login_rate_limit(self) -> None:
        status, data, _ = self.request(
            "/api/login",
            method="POST",
            raw=b'username=employee&password=x',
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual((status, data["code"]), (415, "INVALID_REQUEST"))

        for _ in range(server.AUTH.max_failures):
            status, data, _ = self.request("/api/login", method="POST", payload={"username": "employee", "password": "wrong"})
            self.assertEqual((status, data["code"]), (401, "INVALID_CREDENTIALS"))
        status, data, headers = self.request("/api/login", method="POST", payload={"username": "employee", "password": "EmployeeDemo!2026"})
        self.assertEqual((status, data["code"]), (429, "RATE_LIMITED"))
        self.assertIsNotNone(headers.get("Retry-After"))

    def test_rate_limit_lock_lasts_for_reported_interval(self) -> None:
        for timestamp in (0, 10, 20, 30, 40):
            with patch("security.time.time", return_value=timestamp):
                session, outcome, _ = server.AUTH.authenticate("employee", "wrong", "clock-test")
            self.assertIsNone(session)
            self.assertEqual(outcome, "invalid_credentials")
        with patch("security.time.time", return_value=61):
            session, outcome, retry_after = server.AUTH.authenticate("employee", "EmployeeDemo!2026", "clock-test")
        self.assertIsNone(session)
        self.assertEqual(outcome, "rate_limited")
        self.assertEqual(retry_after, 39)

    def test_partial_password_configuration_is_rejected(self) -> None:
        clean_env = dict(os.environ)
        clean_env.pop("CQ_EMPLOYEE_PASSWORD", None)
        clean_env.pop("CQ_HR_PASSWORD", None)
        clean_env["CQ_EMPLOYEE_PASSWORD"] = "DifferentEmployee!2026"
        with patch.dict(os.environ, clean_env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Set both"):
                server.AuthManager()

    def test_rate_limit_audit_events_are_coalesced(self) -> None:
        for index in range(25):
            server.AUTH.audit("login_rate_limited", f"attacker-{index}", "unknown", "denied", "Concurrent attempt limit")
        events = [item for item in server.AUTH.security_summary()["audit_events"] if item["action"] == "login_rate_limited"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["count"], 25)
        self.assertIn("25 requests", events[0]["detail"])

    def test_new_login_rotates_previous_session(self) -> None:
        jar_one = http.cookiejar.CookieJar()
        jar_two = http.cookiejar.CookieJar()
        opener_one = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar_one))
        opener_two = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar_two))
        self.login("employee", "EmployeeDemo!2026", opener_one)
        self.assertEqual(self.request("/api/me", opener=opener_one)[0], 200)
        self.login("employee", "EmployeeDemo!2026", opener_two)
        self.assertEqual(self.request("/api/me", opener=opener_one)[0], 401)
        self.assertEqual(self.request("/api/me", opener=opener_two)[0], 200)

    def test_hr_profile_access_is_audited(self) -> None:
        self.login("hr", "HRDemo!2026#")
        self.assertEqual(self.request("/api/employee?id=E0028")[0], 200)
        status, summary, _ = self.request("/api/security")
        self.assertEqual(status, 200)
        event = next(item for item in summary["audit_events"] if item["action"] == "profile_viewed")
        self.assertEqual(event["actor"], "hr")
        self.assertEqual(event["detail"], "E0028")

    def test_deep_json_and_replaced_event_graph_are_rejected(self) -> None:
        deep = b'{"x":' + b"[" * 1_200 + b"0" + b"]" * 1_200 + b"}"
        status, data, _ = self.request("/api/login", method="POST", raw=deep)
        self.assertEqual((status, data["code"]), (400, "INVALID_REQUEST"))

        csrf, _ = self.login("hr", "HRDemo!2026#")
        event = {
            "event_id": "EV_ONLY",
            "title": "Only event",
            "type": "course",
            "audience": {"roles": []},
            "skills": [{"skill_id": "SK_COMMUNICATION", "gain": 1, "max_level": 5}],
            "duration_hours": 1,
            "voluntary": True,
        }
        status, data, _ = self.request("/api/upload", method="POST", payload={"events": [event]}, headers={"X-CSRF-Token": csrf})
        self.assertEqual((status, data["code"]), (422, "VALIDATION_ERROR"))
        self.assertEqual(self.request("/api/hr")[1]["data_source"], "Встроенный демо-набор")


if __name__ == "__main__":
    unittest.main()
