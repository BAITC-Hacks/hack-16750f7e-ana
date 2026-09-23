import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
from threading import Thread
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from auth import AuthStore

from coaching import coaching
from engine import CareerEngine


class ProgressTest(unittest.TestCase):
    def setUp(self):
        self.engine = CareerEngine()

    def test_concurrent_completion_awards_once(self):
        before = self.engine.gamification("E0028")["xp"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.engine.complete("E0028", "EV_SYSTEM_DESIGN"), range(4)))
        self.assertEqual(sum(item["reward"]["xp"] for item in results), 100)
        self.assertEqual(self.engine.gamification("E0028")["xp"], before + 100)
        self.assertEqual(self.engine.employee("E0028")["skills"]["SK_SYSTEM_DESIGN"], 3)
        self.assertEqual(results[-1]["gamification"]["level"], 2)

    def test_weekly_goal_handles_duplicates_invalid_and_future_dates(self):
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        self.engine.history = [
            {"employee_id": "E0028", "event_id": event, "status": status, "date": day}
            for event, status, day in [
                ("a", "completed", str(today)), ("a", "completed", str(today)),
                ("b", "completed", str(monday)), ("c", "completed", str(monday - timedelta(days=1))),
                ("d", "completed", "invalid"), ("e", "skipped", str(today)),
                ("f", "completed", str(today + timedelta(days=1))),
            ]
        ]
        progress = self.engine.gamification("E0028")
        self.assertEqual(progress["weekly_completed"], 2)
        self.assertEqual(progress["xp"], 500)
        self.assertTrue(all(badge["earned"] for badge in progress["badges"]))
        self.assertEqual(self.engine.gamification("E0001")["xp"], 0)

    def test_invalid_activity_does_not_award_or_mutate(self):
        before = self.engine.employee_view("E0028")
        with self.assertRaises(ValueError):
            self.engine.complete("E0028", "EV_SQL_CASE")
        self.assertEqual(before, self.engine.employee_view("E0028"))

    def test_completion_never_lowers_skill(self):
        self.engine.employee("E0028")["skills"]["SK_SYSTEM_DESIGN"] = 5
        self.engine.complete("E0028", "EV_SYSTEM_DESIGN")
        self.assertEqual(self.engine.employee("E0028")["skills"]["SK_SYSTEM_DESIGN"], 5)

    def test_reset_restores_progress(self):
        before = self.engine.gamification("E0028")
        self.engine.complete("E0028", "EV_SYSTEM_DESIGN")
        self.engine.reset()
        self.assertEqual(before, self.engine.gamification("E0028"))


class CoachingTest(unittest.TestCase):
    def setUp(self):
        self.engine = CareerEngine()
        self.profile = self.engine.employee_view("E0028")

    @patch.dict("os.environ", {}, clear=True)
    @patch("coaching.urlopen")
    def test_offline_plan_is_actionable_and_does_not_call_network(self, send):
        result = coaching(self.profile, generate=True)
        self.assertEqual(result["source"], "local")
        self.assertEqual(result["plans"][0]["skill_id"], "SK_SYSTEM_DESIGN")
        self.assertTrue(result["plans"][0]["practice"])
        send.assert_not_called()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "test-model", "CQ_ALLOW_EXTERNAL_AI": "1"})
    @patch("coaching.urlopen")
    def test_ai_advice_uses_minimal_context_and_cannot_mutate_profile(self, send):
        response = MagicMock()
        response.read.return_value = json.dumps({"status": "completed", "output": [
            {"type": "reasoning"}, {"type": "message", "content": [{"type": "output_text", "text": "Practice plan"}]}]}).encode()
        send.return_value.__enter__.return_value = response
        result = coaching(self.profile, generate=True)
        self.assertEqual(result["source"], "ai")
        self.assertEqual(result["advice"], "Practice plan")
        request = json.loads(send.call_args.args[0].data)
        self.assertFalse(request["store"])
        self.assertNotIn("E0028", request["input"])
        self.assertNotIn(self.profile["employee"]["name"], request["input"])
        self.assertEqual(self.profile, self.engine.employee_view("E0028"))

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "test-model", "CQ_ALLOW_EXTERNAL_AI": "1"})
    @patch("coaching.urlopen", side_effect=TimeoutError)
    def test_ai_failure_preserves_local_plan(self, send):
        result = coaching(self.profile, generate=True)
        self.assertEqual(result["source"], "local")
        self.assertTrue(result["plans"])
        self.assertIn("недоступен", result["message"])

    def test_no_gaps_has_maintenance_advice(self):
        self.profile["skills"] = []
        result = coaching(self.profile)
        self.assertEqual(result["plans"], [])
        self.assertTrue(result["advice"])


class ApiTest(unittest.TestCase):
    @patch.dict("os.environ", {"CQ_QUIET": "1"}, clear=True)
    def test_profile_completion_and_coach_endpoints(self):
        import server
        auth = AuthStore([("employee", "test-password", "employee", "E0028")])
        token, session = auth.login("employee", "test-password", "test")
        headers = {"Cookie": f"cq_session={token}", "X-CSRF-Token": session["csrf"], "Content-Type": "application/json"}
        with patch.object(server, "ENGINE", CareerEngine()), patch.object(server, "AUTH", auth):
            http = ThreadingHTTPServer(("127.0.0.1", 0), server.CareerQuestHandler)
            thread = Thread(target=http.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{http.server_port}"
            try:
                with urlopen(Request(base + "/api/employee?id=E0028", headers=headers)) as response:
                    profile = json.load(response)
                self.assertIn("gamification", profile)
                self.assertEqual(profile["coaching"]["source"], "local")
                for path, payload in [
                    ("/api/complete", {"employee_id": "E0028", "event_id": "EV_SYSTEM_DESIGN"}),
                    ("/api/coach", {"employee_id": "E0028"}),
                ]:
                    request = Request(base + path, data=json.dumps(payload).encode(),
                                      headers=headers)
                    with urlopen(request) as response:
                        result = json.load(response)
                    if path == "/api/complete":
                        self.assertEqual(result["reward"]["xp"], 100)
                        self.assertIn("coaching", result)
                    else:
                        self.assertTrue(result["plans"])
            finally:
                http.shutdown()
                http.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
