import unittest
from copy import deepcopy
from unittest.mock import patch
from engine import CareerEngine, parse_csv_text


def kit():
    return {
        "employees": {"employee_id": "KIT", "name": "Test", "role": "Tester", "grade": "Middle", "skills": {"S": 1}},
        "skills": {"S": {"name": "Testing", "requirements": {"Tester": {"Senior": 3}}}},
        "events": {"E": {"title": "Practice", "skill_gains": [{"skill_id": "S", "gain": 1, "max_level": 5}], "duration_hours": 2}},
        "activity_history": parse_csv_text("employee_id,event_id,status,on_time,date\nKIT,E,skipped,false,2026-01-01"),
    }


class ImportTest(unittest.TestCase):
    def test_four_file_kit_normalizes_and_recommends(self):
        engine = CareerEngine()
        result = engine.upload_bundle(kit())
        self.assertEqual(engine.skills[0]["skill_id"], "S")
        self.assertEqual(engine.events[0]["event_id"], "E")
        self.assertEqual(engine.employee_view("KIT")["recommendations"][0]["event_id"], "E")
        self.assertFalse(engine.history[-1]["on_time"])
        self.assertIn("counts", result)
        self.assertTrue(result["warnings"])

    def test_invalid_bundle_is_atomic(self):
        for field, value in [("employees", [{"employee_id": "BROKEN", "role": [], "skills": {}}]),
                             ("events", {"E": {"skills": [{"skill_id": "S", "gain": -1}]}}),
                             ("skills", {"S": {"name": "S", "requirements": {"Tester": {"Senior": 9}}}}),
                             ("history", [{"employee_id": "unknown", "event_id": "E", "status": "completed"}])]:
            engine = CareerEngine()
            before = deepcopy({k: v for k, v in vars(engine).items() if k != "lock"})
            bundle = kit()
            bundle.pop("activity_history")
            bundle[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                engine.upload_bundle(bundle)
            self.assertEqual(before, {k: v for k, v in vars(engine).items() if k != "lock"})

    def test_preflight_failure_does_not_commit(self):
        engine = CareerEngine()
        before = deepcopy(engine.employees)
        revision = engine.revision
        with patch.object(CareerEngine, "employee_view", side_effect=ValueError("failed")):
            with self.assertRaises(ValueError):
                engine.upload_bundle(kit())
        self.assertEqual(before, engine.employees)
        self.assertEqual(revision, engine.revision)

    def test_invalid_levels_and_csv_flags(self):
        for level in [True, "3", -1, 6, 1.5, float("nan"), 10 ** 1000]:
            bundle = kit()
            bundle["employees"]["skills"]["S"] = level
            with self.subTest(level=level), self.assertRaises(ValueError):
                CareerEngine().upload_bundle(bundle)

    def test_empty_history_clears_only_imported_employee(self):
        engine = CareerEngine()
        employee = deepcopy(engine.employee("E0028"))
        engine.upload_bundle({"employees": employee, "history": []})
        self.assertFalse(any(row["employee_id"] == "E0028" for row in engine.history))
        self.assertTrue(engine.history)

    def test_csv_converted_false_is_not_truthy(self):
        bundle = kit()
        bundle["activity_history"][0]["on_time"] = "false"
        engine = CareerEngine()
        engine.upload_bundle(bundle)
        self.assertIs(engine.history[-1]["on_time"], False)

    def test_list_catalogs_and_alternative_requirements(self):
        bundle = kit()
        bundle["skills"] = [{"skill_id": "S", "name": "Testing", "grade_requirements": {"Senior": 3}}]
        bundle["events"] = [{"event_id": "E", "skills": [{"skill_id": "S", "gain": 1, "max_level": 5}], "duration_hours": 0}]
        engine = CareerEngine()
        engine.upload_bundle(bundle)
        self.assertTrue(engine.employee_view("KIT")["recommendations"])
        self.assertEqual(engine.planner("KIT", 1)["options"][0]["duration_hours"], 0)


if __name__ == "__main__":
    unittest.main()
