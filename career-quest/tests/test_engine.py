import unittest

from engine import CareerEngine


class RecommendationEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = CareerEngine()

    def test_multifactor_profile_prefers_critical_grade_gap(self):
        result = self.engine.employee_view("E0028")
        self.assertEqual(result["recommendations"][0]["event_id"], "EV_SYSTEM_DESIGN")
        self.assertEqual(result["recommendations"][0]["affected_skills"][0]["skill_id"], "SK_SYSTEM_DESIGN")
        self.assertEqual(len(result["recommendations"][0]["factors"]), 4)
        self.assertNotEqual(result["recommendations"][0]["affected_skills"][0]["skill_id"], "SK_PUBLIC_SPEAKING")

    def test_complete_updates_skill_and_readiness(self):
        before = self.engine.employee_view("E0028")
        after = self.engine.complete("E0028", "EV_SYSTEM_DESIGN")
        self.assertGreater(after["readiness"], before["readiness"])
        self.assertEqual(
            next(row for row in after["skills"] if row["skill_id"] == "SK_SYSTEM_DESIGN")["current"],
            3,
        )
        self.assertNotIn("EV_SYSTEM_DESIGN", [item["event_id"] for item in after["recommendations"]])

    def test_uploaded_verification_profile_is_not_ranked_by_minimum_skill(self):
        self.engine.upload_bundle(
            {
                "employees": [
                    {
                        "employee_id": "JURY01",
                        "role": "Backend Engineer",
                        "grade": "Middle",
                        "skills": {
                            "SK_PYTHON": 3,
                            "SK_SYSTEM_DESIGN": 1,
                            "SK_PUBLIC_SPEAKING": 2,
                            "SK_COMMUNICATION": 3,
                        },
                    }
                ],
                "activity_history": [
                    {"employee_id": "JURY01", "event_id": "EV_PUBLIC_SPEAKING", "status": "skipped"},
                    {"employee_id": "JURY01", "event_id": "EV_PUBLIC_SPEAKING", "status": "declined"},
                ],
            }
        )
        result = self.engine.employee_view("JURY01")
        self.assertEqual(result["recommendations"][0]["affected_skills"][0]["skill_id"], "SK_SYSTEM_DESIGN")

    def test_hr_view_contains_required_metrics(self):
        result = self.engine.hr_view()
        self.assertEqual(result["employees"], 200)
        self.assertTrue(result["skill_gaps"])
        self.assertIn("completion_rate", result)
        self.assertIn("without_step", result)


if __name__ == "__main__":
    unittest.main()
