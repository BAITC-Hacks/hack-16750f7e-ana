import unittest
from copy import deepcopy

from engine import CareerEngine


class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.engine = CareerEngine()

    def test_time_budget_changes_recommendation_with_explanation(self):
        full = self.engine.planner("E0028", 8)
        short = self.engine.planner("E0028", 5)
        self.assertEqual(full["options"][0]["event_id"], "EV_SYSTEM_DESIGN")
        self.assertNotEqual(short["options"][0]["event_id"], "EV_SYSTEM_DESIGN")
        self.assertTrue(all(item["duration_hours"] <= 5 for item in short["options"]))
        self.assertIn("без ограничения", short["message"])
        empty = self.engine.planner("E0028", 1)
        self.assertEqual(empty["options"], [])
        self.assertIn("не помещается", empty["message"])

    def test_simulation_matches_completion_without_writing_progress(self):
        before = deepcopy(self.engine.employee_view("E0028"))
        history = deepcopy(self.engine.history)
        result = self.engine.simulate("E0028", ["EV_SYSTEM_DESIGN", "EV_PUBLIC_SPEAKING"], 16)
        self.assertEqual(before, self.engine.employee_view("E0028"))
        self.assertEqual(history, self.engine.history)
        self.engine.complete("E0028", "EV_SYSTEM_DESIGN")
        after = self.engine.complete("E0028", "EV_PUBLIC_SPEAKING")
        self.assertEqual(result["after"], after["readiness"])
        self.assertEqual(result["hours"], 11)

    def test_overlapping_steps_do_not_double_count_readiness(self):
        self.engine.history = []
        self.engine.employee("E0028")["skills"]["SK_SYSTEM_DESIGN"] = 3
        ids = ["EV_SYSTEM_DESIGN", "EV_ARCH_MENTOR"]
        options = self.engine.planner("E0028", 16)["options"]
        naive_delta = sum(item["readiness_delta"] for item in options if item["event_id"] in ids)
        result = self.engine.simulate("E0028", ids, 16)
        self.assertLess(result["after"] - result["before"], naive_delta)
        self.assertEqual(result["steps"][1]["delta"], 0)
        self.assertEqual(result["closed_gaps"], 1)

    def test_invalid_or_stale_choices_are_rejected(self):
        for ids, hours in [([], 8), (["EV_SYSTEM_DESIGN"] * 2, 16), ([{}], 8),
                           (["EV_SQL_CASE"], 8), (["EV_SYSTEM_DESIGN", "EV_PUBLIC_SPEAKING"], 8),
                           (["EV_ARCH_MENTOR"], 8), ("EV_SYSTEM_DESIGN", 8)]:
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.engine.simulate("E0028", ids, hours)
        for hours in [0, -1, 41, "nan", "inf", None, True]:
            with self.subTest(hours=hours), self.assertRaises(ValueError):
                self.engine.planner("E0028", hours)

    def test_pause_allows_planning_without_resuming(self):
        self.engine.set_participation("E0028", True)
        self.engine.simulate("E0028", ["EV_SYSTEM_DESIGN"], 8)
        self.assertTrue(self.engine.employee_view("E0028")["participation_paused"])


if __name__ == "__main__":
    unittest.main()
