import unittest
from engine import CareerEngine


class HrAnalyticsTest(unittest.TestCase):
    def test_missing_requirements_are_unavailable(self):
        engine = CareerEngine()
        engine.skills = []
        profile = engine.employee_view("E0028")
        self.assertIsNone(profile["readiness"])
        self.assertTrue(profile["requirements_missing"])
        hr = engine.hr_view()
        self.assertIsNone(hr["average_readiness"])
        self.assertEqual(hr["requirements_missing"], 200)
        self.assertEqual(hr["without_step"], len(hr["employees_without_step"]))
        self.assertEqual(hr["employees_without_step"][0]["reason"], "Отсутствуют требования следующего грейда")

    def test_without_step_reasons_follow_data(self):
        engine = CareerEngine()
        employee = engine.employee("E0028")
        engine.employees = [employee]
        engine.events = []
        result = engine.hr_view()["employees_without_step"][0]
        self.assertEqual(result["employee_id"], "E0028")
        self.assertEqual(result["reason"], "Каталог не содержит активности для роли")
        employee["skills"] = {key: 5 for key in employee["skills"]}
        self.assertEqual(engine.hr_view()["employees_without_step"][0]["reason"], "Нет открытых skill gaps")

    def test_unique_participation_and_status_precedence(self):
        engine = CareerEngine()
        engine.history = [
            {"employee_id": person, "event_id": "EV_SYSTEM_DESIGN", "status": status, "date": day}
            for person, status, day in [("E0028", "completed", "2026-01-01"), ("E0028", "completed", "2026-01-02"),
                                       ("E0028", "declined", "2026-01-03"), ("E0001", "skipped", "2026-01-01"),
                                       ("E0001", "declined", "2026-02-01")]]
        item = next(row for row in engine.hr_view()["activity_participation"] if row["event_id"] == "EV_SYSTEM_DESIGN")
        self.assertEqual((item["participants"], item["completed"], item["skipped"], item["declined"]), (2, 1, 0, 1))
        self.assertEqual(item["completion_rate"], 50)
        self.assertGreater(item["eligible_employees"], 0)


if __name__ == "__main__":
    unittest.main()
