import unittest
from copy import deepcopy
from datetime import date, timedelta

from engine import CAREER_TRACKS, CareerEngine


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

    def test_hr_view_exposes_two_hypothesis_career_tracks(self):
        result = self.engine.hr_view()
        self.assertEqual(result["career_tracks"], CAREER_TRACKS)
        tracks = {track["track_id"]: track for track in result["career_tracks"]}
        self.assertEqual(
            tracks["retail_branch"]["path"],
            ["Операционист", "Менеджер по обслуживанию", "Кредитный эксперт", "Руководитель отделения"],
        )
        self.assertEqual(tracks["sme"]["status"], "hypothesis_for_business_validation")

    def test_retention_summary_is_complete_deterministic_and_excludes_protected_attributes(self):
        first = self.engine.hr_view()
        second = self.engine.hr_view()
        summary = first["retention_summary"]
        self.assertEqual(first["retention_cases"], second["retention_cases"])
        self.assertEqual(summary["total_evaluated"], 200)
        self.assertEqual(summary["low"] + summary["medium"] + summary["high"], 200)
        self.assertEqual(summary["high_risk_count"], summary["high"])
        self.assertEqual(summary["protected_attributes_used"], [])
        self.assertEqual(
            set(summary["features_used"]),
            {
                "readiness",
                "participation_rate",
                "tenure_months",
                "has_next_step",
                "recent_missed_declined_or_skipped",
            },
        )

    def test_retention_case_explains_progression_and_recent_non_completion_signals(self):
        self.engine.employees.append(
            {
                "employee_id": "RETENTION1",
                "name": "Synthetic Retention Profile",
                "role": "Backend Engineer",
                "grade": "Middle",
                "tenure_months": 60,
                "skills": {
                    "SK_PYTHON": 5,
                    "SK_SYSTEM_DESIGN": 5,
                    "SK_PUBLIC_SPEAKING": 5,
                    "SK_COMMUNICATION": 5,
                },
            }
        )
        self.engine.history.extend(
            [
                {
                    "employee_id": "RETENTION1",
                    "event_id": "EV_SYSTEM_DESIGN",
                    "status": "declined",
                    "on_time": False,
                    "date": str(date.today() - timedelta(days=20)),
                },
                {
                    "employee_id": "RETENTION1",
                    "event_id": "EV_PUBLIC_SPEAKING",
                    "status": "missed",
                    "on_time": False,
                    "date": str(date.today() - timedelta(days=50)),
                },
            ]
        )
        result = self.engine.hr_view()
        case = next(item for item in result["retention_cases"] if item["employee_id"] == "RETENTION1")
        signal_keys = {signal["key"] for signal in case["signals"]}
        self.assertEqual(case["risk_band"], "high")
        self.assertFalse(case["feature_snapshot"]["has_next_step"])
        self.assertEqual(case["feature_snapshot"]["recent_missed_declined_or_skipped"], 2)
        self.assertTrue({"no_next_step", "ready_without_movement", "recent_non_completion"} <= signal_keys)

    def test_completion_never_reduces_an_existing_skill(self):
        self.engine.employees.append(
            {
                "employee_id": "MONO1",
                "name": "Monotonic Test",
                "role": "Backend Engineer",
                "grade": "Middle",
                "tenure_months": 12,
                "skills": {"SK_PYTHON": 5, "SK_SYSTEM_DESIGN": 0, "SK_PUBLIC_SPEAKING": 3, "SK_COMMUNICATION": 3},
            }
        )
        self.engine.events.append(
            {
                "event_id": "EV_MONO",
                "title": {"ru": "Проверка монотонности"},
                "type": "project",
                "audience": {"roles": ["Backend Engineer"]},
                "skills": [
                    {"skill_id": "SK_SYSTEM_DESIGN", "gain": 1, "max_level": 5},
                    {"skill_id": "SK_PYTHON", "gain": 1, "max_level": 1},
                ],
                "duration_hours": 2,
                "voluntary": True,
            }
        )
        self.engine.complete("MONO1", "EV_MONO")
        self.assertEqual(self.engine.employee("MONO1")["skills"]["SK_PYTHON"], 5)

    def test_terminal_grade_does_not_recommend_same_grade(self):
        self.engine.employees.append(
            {
                "employee_id": "LEAD1",
                "name": "Lead Test",
                "role": "Backend Engineer",
                "grade": "Lead",
                "tenure_months": 80,
                "skills": {"SK_PYTHON": 1, "SK_SYSTEM_DESIGN": 1},
            }
        )
        result = self.engine.employee_view("LEAD1")
        self.assertTrue(result["terminal_grade"])
        self.assertEqual(result["target_grade"], "Экспертный трек")
        self.assertEqual(result["readiness"], 100.0)
        self.assertEqual(result["recommendations"], [])

    def test_validation_rejects_dangling_graph_unknown_rubric_and_duplicate_gain(self):
        with self.assertRaisesRegex(ValueError, "История ссылается"):
            self.engine.upload_bundle_validated({"events": [deepcopy(self.engine.events[0])]})

        with self.assertRaisesRegex(ValueError, "не настроены требования"):
            self.engine.upload_bundle_validated(
                {
                    "employees": [{
                        "employee_id": "TYPO1",
                        "name": "Role Typo",
                        "role": "Backend Enginer",
                        "grade": "Middle",
                        "tenure_months": 10,
                        "skills": {},
                    }]
                }
            )

        events = deepcopy(self.engine.events)
        events.append(
            {
                "event_id": "EV_DUP_GAIN",
                "title": "Duplicate gain",
                "type": "course",
                "audience": {"roles": ["Backend Engineer"]},
                "skills": [
                    {"skill_id": "SK_SYSTEM_DESIGN", "gain": 1, "max_level": 5},
                    {"skill_id": "SK_SYSTEM_DESIGN", "gain": 1, "max_level": 5},
                ],
                "duration_hours": 2,
                "voluntary": True,
            }
        )
        with self.assertRaisesRegex(ValueError, "повторяет skill_id"):
            self.engine.upload_bundle_validated({"events": events})

    def test_history_is_sorted_newest_first(self):
        history = self.engine.employee_view("E0028")["history"]
        dates = [row["date"] for row in history]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_quarterly_cooldown_prevents_false_lifetime_dead_end(self):
        self.engine.history.append(
            {
                "employee_id": "E0028",
                "event_id": "EV_SYSTEM_DESIGN",
                "status": "completed",
                "on_time": True,
                "date": str(date.today() - timedelta(days=91)),
            }
        )
        recommendations = self.engine.recommendations("E0028", limit=len(self.engine.events))
        self.assertIn("EV_SYSTEM_DESIGN", [item["event_id"] for item in recommendations])
        self.engine.complete("E0028", "EV_SYSTEM_DESIGN")
        self.assertNotIn(
            "EV_SYSTEM_DESIGN",
            [item["event_id"] for item in self.engine.recommendations("E0028", limit=len(self.engine.events))],
        )


if __name__ == "__main__":
    unittest.main()
