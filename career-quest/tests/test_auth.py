import json
import os
import unittest
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import server
from auth import AuthStore
from coaching import coaching
from engine import CareerEngine


class AccessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth = AuthStore([
            ("employee", "employee-secret", "employee", "E0028"),
            ("other", "other-secret", "employee", "E0001"),
            ("hr", "hr-secret", "hr", None),
        ])
        cls.auth_patch = patch.object(server, "AUTH", cls.auth)
        cls.auth_patch.start()
        cls.quiet_patch = patch.dict(os.environ, {"CQ_QUIET": "1"})
        cls.quiet_patch.start()
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), server.CareerQuestHandler)
        cls.base = f"http://127.0.0.1:{cls.http.server_port}"
        cls.thread = Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        cls.auth_patch.stop()
        cls.quiet_patch.stop()

    def setUp(self):
        self.engine_patch = patch.object(server, "ENGINE", CareerEngine())
        self.engine_patch.start()
        self.addCleanup(self.engine_patch.stop)
        self.auth.attempts.clear()
        self.auth.sessions.clear()

    def request(self, path, payload=None, headers=None):
        headers = {"Content-Type": "application/json", **(headers or {})}
        request = Request(self.base + path, headers=headers,
                          data=None if payload is None else json.dumps(payload).encode())
        try:
            response = urlopen(request)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            content = json.loads(body) if "application/json" in response.headers.get("Content-Type", "") else body
            return response.status, content, response.headers

    def login(self, user="employee", **extra):
        status, result, headers = self.request("/api/login", {"username": user, "password": f"{user}-secret", **extra})
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        return {"Cookie": cookie.split(";", 1)[0], "X-CSRF-Token": result["csrf"]}

    def test_anonymous_cannot_read_or_modify(self):
        for path in ["/api/employee?id=E0028", "/api/employees", "/api/hr", "/api/session"]:
            self.assertEqual(self.request(path)[0], 401)
        self.assertEqual(self.request("/api/complete", {"employee_id": "E0028", "event_id": "EV_SYSTEM_DESIGN"})[0], 401)

    def test_employee_cannot_impersonate_or_access_hr(self):
        headers = self.login(role="hr", employee_id="E0001")
        status, session, _ = self.request("/api/session", headers=headers)
        self.assertEqual(session["user"]["role"], "employee")
        self.assertEqual(session["user"]["employee_id"], "E0028")
        self.assertEqual(self.request("/api/employee", headers=headers)[0], 200)
        for path in ["/api/employee?id=E0001", "/api/hr", "/api/employees"]:
            self.assertEqual(self.request(path, headers=headers)[0], 403)
        for path, payload in [("/api/upload", {}), ("/api/reset", {}),
                              ("/api/coach", {"employee_id": "E0001"}),
                              ("/api/complete", {"employee_id": "E0001", "event_id": "EV_SYSTEM_DESIGN"}),
                              ("/api/participation", {"employee_id": "E0001", "paused": True})]:
            self.assertEqual(self.request(path, payload, headers)[0], 403)
        other = self.login("other")
        self.assertEqual(self.request("/api/employee?id=E0028", headers=other)[0], 403)

    def test_hr_reads_analytics_but_cannot_complete_for_employee(self):
        headers = self.login("hr")
        for path in ["/api/hr", "/api/employees", "/api/employee?id=E0028"]:
            self.assertEqual(self.request(path, headers=headers)[0], 200)
        self.assertEqual(self.request("/api/complete", {"employee_id": "E0028", "event_id": "EV_SYSTEM_DESIGN"}, headers)[0], 403)
        self.assertEqual(self.request("/api/reset", {}, headers)[0], 200)

    def test_csrf_logout_and_session_expiry(self):
        headers = self.login()
        self.assertEqual(self.request("/api/logout", {}, {"Cookie": headers["Cookie"]})[0], 403)
        self.assertEqual(self.request("/api/logout", {}, {**headers, "Origin": "https://other.example"})[0], 403)
        self.assertEqual(self.request("/api/logout", {}, headers)[0], 200)
        self.assertEqual(self.request("/api/session", headers=headers)[0], 401)
        headers = self.login()
        token = headers["Cookie"].split("=", 1)[1]
        self.auth.sessions[token]["expires"] = 0
        self.assertEqual(self.request("/api/session", headers=headers)[0], 401)

    def test_login_errors_and_throttling(self):
        for _ in range(5):
            self.assertEqual(self.request("/api/login", {"username": "employee", "password": "wrong"})[0], 401)
        self.assertEqual(self.request("/api/login", {"username": "employee", "password": "employee-secret"})[0], 429)

    def test_private_files_are_not_served(self):
        for path in ["/server.py", "/auth.py", "/sample_upload/employees.json", "/%2e%2e/.git/config"]:
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request("/app.js")[0], 200)

    def test_ai_endpoint_requires_session_csrf_and_own_profile(self):
        payload = {"employee_id": "E0028"}
        self.assertEqual(self.request("/api/recommendations/ai", payload)[0], 401)
        headers = self.login()
        self.assertEqual(self.request("/api/recommendations/ai", payload, {"Cookie": headers["Cookie"]})[0], 403)
        self.assertEqual(self.request("/api/recommendations/ai", {"employee_id": "E0001"}, headers)[0], 403)
        with patch.dict(os.environ, {"CQ_ALLOW_EXTERNAL_AI": "0"}):
            status, result, _ = self.request("/api/recommendations/ai", payload, headers)
        self.assertEqual(status, 200)
        self.assertFalse(result["ai_used"])

    def test_ai_endpoint_can_apply_valid_model_order(self):
        from ai_recommender import AIRecommender
        from test_ai_recommender import answer
        headers = self.login()
        with patch.object(server, "AI_RECOMMENDER", AIRecommender()), patch.dict(os.environ, {
                "OPENAI_API_KEY": "test", "OPENAI_MODEL": "test", "CQ_ALLOW_EXTERNAL_AI": "1"}), patch(
                "ai_recommender.request_ranking", side_effect=lambda context, *args: answer(context)):
            _, profile, _ = self.request("/api/employee", headers=headers)
            status, result, _ = self.request("/api/recommendations/ai", {"employee_id": "E0028"}, headers)
        self.assertEqual(status, 200)
        self.assertTrue(result["ai_used"])
        self.assertNotEqual(result["recommendations"][0]["event_id"], profile["recommendations"][0]["event_id"])

    def test_import_errors_are_400_and_do_not_change_profile(self):
        headers = self.login("hr")
        before = server.ENGINE.employee_view("E0028")
        status, result, _ = self.request("/api/upload", {"employees": {"employee_id": "E0028", "role": []}}, headers)
        self.assertEqual(status, 400)
        self.assertIn("Импорт", result["error"])
        self.assertEqual(before, server.ENGINE.employee_view("E0028"))

    def test_planner_and_simulator_enforce_profile_access(self):
        self.assertEqual(self.request("/api/planner?id=E0028")[0], 401)
        headers = self.login()
        self.assertEqual(self.request("/api/planner?hours=4", headers=headers)[0], 200)
        self.assertEqual(self.request("/api/planner?hours=nan", headers=headers)[0], 400)
        self.assertEqual(self.request("/api/planner?id=E0001", headers=headers)[0], 403)
        payload = {"employee_id": "E0001", "event_ids": ["EV_SYSTEM_DESIGN"], "hours": 8}
        self.assertEqual(self.request("/api/simulate", payload, headers)[0], 403)
        payload["employee_id"] = "E0028"
        before = server.ENGINE.employee_view("E0028")
        self.assertEqual(self.request("/api/simulate", payload, headers)[0], 200)
        self.assertEqual(before, server.ENGINE.employee_view("E0028"))
        self.assertEqual(self.request("/api/simulate", payload, self.login("hr"))[0], 200)

    def test_pause_preserves_progress_and_suppresses_hr_signal(self):
        headers = self.login()
        employee = server.ENGINE.employee("E0028")
        employee["skills"] = {skill: 0 for skill in employee["skills"]}
        self.assertIn("E0028", [row["employee_id"] for row in server.ENGINE.hr_view()["watchlist"]])
        before = server.ENGINE.gamification("E0028")
        self.assertEqual(self.request("/api/participation", {"paused": True}, headers)[0], 200)
        self.assertEqual(self.request("/api/complete", {"event_id": "EV_SYSTEM_DESIGN"}, headers)[0], 400)
        self.assertEqual(before, server.ENGINE.gamification("E0028"))
        hr = server.ENGINE.hr_view()
        self.assertEqual(hr["paused_employees"], 1)
        self.assertNotIn("E0028", [row["employee_id"] for row in hr["watchlist"]])
        self.assertEqual(self.request("/api/participation", {"paused": False}, headers)[0], 200)
        status, result, _ = self.request("/api/complete", {"event_id": "EV_SYSTEM_DESIGN"}, headers)
        self.assertEqual(status, 200)
        self.assertEqual(result["reward"]["xp"], 100)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "key", "OPENAI_MODEL": "model", "CQ_ALLOW_EXTERNAL_AI": "0"})
    @patch("coaching.urlopen")
    def test_internal_mode_does_not_send_profile_to_external_ai(self, send):
        result = coaching(server.ENGINE.employee_view("E0028"), generate=True)
        self.assertFalse(result["ai_available"])
        send.assert_not_called()


class ServerBindingTest(unittest.TestCase):
    def test_second_server_cannot_share_port(self):
        first = server.CareerQuestServer(("127.0.0.1", 0), server.CareerQuestHandler)
        try:
            with self.assertRaises(OSError):
                second = server.CareerQuestServer(first.server_address, server.CareerQuestHandler)
                second.server_close()
        finally:
            first.server_close()


if __name__ == "__main__":
    unittest.main()
