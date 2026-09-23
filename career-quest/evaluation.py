"""Offline business-invariant benchmark. Run: python evaluation.py [--json]."""

import argparse
from copy import deepcopy
import json
import sys
from engine import CareerEngine


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def evaluate_scenario(name):
    engine = CareerEngine()
    person = engine.employee("E0028")
    if name == "critical_gap_not_minimum_skill":
        view = engine.employee_view("E0028")
        weakest = min(view["skills"], key=lambda item: item["current"])["skill_id"]
        check(view["recommendations"][0]["affected_skills"][0]["skill_id"] != weakest, "Minimum skill should not dominate critical gap")
    elif name == "repeated_skips_reduce_priority":
        engine.history = []
        before = {row["event_id"]: row for row in engine.recommendations("E0028", 20)}
        engine.history = [{"employee_id": "E0028", "event_id": "EV_PUBLIC_SPEAKING", "status": "skipped"}] * 3
        after = {row["event_id"]: row for row in engine.recommendations("E0028", 20)}
        check(after["EV_PUBLIC_SPEAKING"]["score"] < before["EV_PUBLIC_SPEAKING"]["score"], "Skips should lower ranking score")
    elif name == "ineligible_role_excluded":
        rows = engine.recommendations("E0028", 20)
        check(bool(rows), "No recommendations to check")
        for row in rows:
            roles = engine.event(row["event_id"])["audience"]["roles"]
            check(not roles or person["role"] in roles, "Ineligible role recommended")
        check("EV_SQL_CASE" not in {row["event_id"] for row in rows}, "Analyst activity leaked")
    elif name == "completed_activity_excluded":
        engine.complete("E0028", "EV_SYSTEM_DESIGN")
        check("EV_SYSTEM_DESIGN" not in {row["event_id"] for row in engine.recommendations("E0028", 20)}, "Completed activity repeated")
    elif name == "employee_without_history":
        engine.history = []
        check(bool(engine.recommendations("E0028")), "Cold-start profile has no recommendation")
    elif name == "equal_gaps_stable_order":
        requirements = engine.requirements(person, "Senior")
        person["skills"] = {key: value - 1 for key, value in requirements.items()}
        engine.history = []
        first = engine.recommendations("E0028")
        check(bool(first) and first == engine.recommendations("E0028"), "Equal-gap order is unstable")
    elif name == "impact_with_excessive_duration":
        engine.history = []
        fast = deepcopy(engine.event("EV_SYSTEM_DESIGN"))
        slow = deepcopy(fast)
        fast.update(event_id="FAST", duration_hours=1)
        slow.update(event_id="SLOW", duration_hours=100)
        engine.events = [slow, fast]
        rows = engine.recommendations("E0028")
        check(rows[0]["event_id"] == "FAST", "Duration ignored in tie on impact")
        check(rows[0]["readiness_delta"] == rows[1]["readiness_delta"] > 0, "Impact setup is invalid")
        check("SLOW" not in {row["event_id"] for row in engine.planner("E0028", 8)["options"]}, "Over-budget activity offered")
    elif name == "all_requirements_closed":
        person["skills"] = engine.requirements(person, "Senior")
        view = engine.employee_view("E0028")
        check(view["readiness"] == 100 and not view["recommendations"], "Closed gaps should not generate steps")
    elif name == "overlapping_activities_no_double_gain":
        engine.history = []
        person["skills"]["SK_SYSTEM_DESIGN"] = 3
        before = deepcopy(engine.employee_view("E0028"))
        result = engine.simulate("E0028", ["EV_SYSTEM_DESIGN", "EV_ARCH_MENTOR"], 16)
        check(result["steps"][1]["delta"] == 0, "Overlap counted twice")
        check(before == engine.employee_view("E0028"), "Simulation mutated profile/XP/history")
    elif name == "no_suitable_activity":
        engine.events = []
        view = engine.employee_view("E0028")
        check(not view["recommendations"] and any(row["gap"] for row in view["skills"]), "Empty catalog falsely closes gaps")
        check(any(row["employee_id"] == "E0028" for row in engine.hr_view()["employees_without_step"]), "HR lost employee without step")
    elif name == "imported_profile":
        employee = deepcopy(person)
        employee.update(employee_id="IMPORTED", name="Synthetic", skills={**person["skills"], "SK_SYSTEM_DESIGN": 1})
        engine.upload_bundle({"employees": employee, "history": []})
        check(bool(engine.employee_view("IMPORTED")["recommendations"]), "Imported profile cannot be ranked")
    elif name == "missing_grade_requirements":
        person["role"] = "Unknown role"
        view = engine.employee_view("E0028")
        check(view["requirements_missing"] and view["readiness"] is None, "Missing requirements falsely yield readiness")
        check(engine.hr_view()["requirements_missing"] == 1, "Missing requirements not counted by HR")


SCENARIOS = ["critical_gap_not_minimum_skill", "repeated_skips_reduce_priority", "ineligible_role_excluded",
             "completed_activity_excluded", "employee_without_history", "equal_gaps_stable_order",
             "impact_with_excessive_duration", "all_requirements_closed", "overlapping_activities_no_double_gain",
             "no_suitable_activity", "imported_profile", "missing_grade_requirements"]


def run_evaluation():
    results = []
    for name in SCENARIOS:
        try:
            evaluate_scenario(name)
            results.append({"scenario": name, "passed": True})
        except Exception as exc:
            results.append({"scenario": name, "passed": False, "error": str(exc)})
    passed = sum(row["passed"] for row in results)
    return {"benchmark": "Recommendation evaluation", "mode": "offline_invariants", "scenarios": len(results),
            "passed": passed, "constraint_violations": len(results) - passed, "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run_evaluation()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Recommendation evaluation\n{result['scenarios']} scenarios\n{result['passed']} passed\nconstraint violations: {result['constraint_violations']}")
        for item in result["results"]:
            print(f"{'PASS' if item['passed'] else 'FAIL'} {item['scenario']} {item.get('error', '')}")
    return int(result["constraint_violations"] > 0)


if __name__ == "__main__":
    sys.exit(main())
