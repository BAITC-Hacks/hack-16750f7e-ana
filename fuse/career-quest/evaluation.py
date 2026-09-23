"""Reproducible offline evaluation for the Career Quest ranking engine.

This is not a claim of model accuracy on real Halyk outcomes.  It checks the
properties an explainable decision-support MVP can verify on its bundled,
synthetic dataset: coverage, deterministic output, valid role targeting,
counterfactual uplift, explanation completeness and behaviour versus a simple
"lowest current skill first" baseline.
"""

from __future__ import annotations

import json
from statistics import mean
from typing import Any

from engine import CareerEngine, is_terminal_grade, next_grade


REQUIRED_FACTOR_KEYS = {"grade", "impact", "history", "format"}
REQUIRED_BREAKDOWN_KEYS = {
    "grade_relevance",
    "trajectory_impact",
    "history_fit",
    "feasibility",
}


def pct(numerator: int, denominator: int) -> float:
    """Return a one-decimal percentage without a division-by-zero branch leak."""

    return round(numerator / denominator * 100, 1) if denominator else 0.0


def lowest_skill_baseline(
    engine: CareerEngine,
    employee: dict[str, Any],
    recommendations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pick the best available activity touching the lowest absolute skill.

    This deliberately represents a common catalogue heuristic, not a straw-man
    random choice: when several activities touch the same lowest skill, it gets
    the highest-scoring one available to that employee.
    """

    target = next_grade(str(employee.get("grade", "Middle")))
    requirements = engine.requirements(employee, target)
    if not requirements:
        return None
    levels = employee.get("skills", {})
    lowest_skill_id = min(requirements, key=lambda skill_id: (int(levels.get(skill_id, 0)), skill_id))
    return next(
        (
            recommendation
            for recommendation in recommendations
            if any(item.get("skill_id") == lowest_skill_id for item in recommendation.get("affected_skills", []))
        ),
        None,
    )


def evaluate() -> dict[str, Any]:
    engine = CareerEngine()
    repeat_engine = CareerEngine()

    eligible = [employee for employee in engine.employees if not is_terminal_grade(str(employee.get("grade", "")))]
    covered = 0
    stable = 0
    all_recommendations: list[tuple[dict[str, Any], dict[str, Any]]] = []
    baseline_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for employee in eligible:
        employee_id = str(employee["employee_id"])
        recommendations = engine.recommendations(employee_id, limit=len(engine.events))
        repeated = repeat_engine.recommendations(employee_id, limit=len(repeat_engine.events))
        if recommendations:
            covered += 1
        if [(item["event_id"], item["score"]) for item in recommendations] == [
            (item["event_id"], item["score"]) for item in repeated
        ]:
            stable += 1

        all_recommendations.extend((employee, item) for item in recommendations)
        if recommendations:
            baseline = lowest_skill_baseline(engine, employee, recommendations)
            if baseline:
                baseline_pairs.append((recommendations[0], baseline))

    explanation_complete = 0
    role_eligible = 0
    positive_counterfactual = 0
    for employee, recommendation in all_recommendations:
        factors = {item.get("key") for item in recommendation.get("factors", [])}
        breakdown = set(recommendation.get("score_breakdown", {}))
        if REQUIRED_FACTOR_KEYS <= factors and REQUIRED_BREAKDOWN_KEYS <= breakdown:
            explanation_complete += 1

        event = engine.event(str(recommendation.get("event_id"))) or {}
        audience = event.get("audience", {})
        roles = audience.get("roles", []) if isinstance(audience, dict) else []
        if not roles or employee.get("role") in roles:
            role_eligible += 1

        if float(recommendation.get("projected_readiness", 0)) > float(recommendation.get("current_readiness", 0)):
            positive_counterfactual += 1

    different_from_baseline = sum(top["event_id"] != baseline["event_id"] for top, baseline in baseline_pairs)
    score_advantages = [float(top["score"]) - float(baseline["score"]) for top, baseline in baseline_pairs]
    top_readiness_deltas = [float(top["readiness_delta"]) for top, _ in baseline_pairs]
    baseline_readiness_deltas = [float(baseline["readiness_delta"]) for _, baseline in baseline_pairs]

    challenge = engine.employee_view("E0028")
    challenge_employee = engine.employee("E0028")
    challenge_all = engine.recommendations("E0028", limit=len(engine.events))
    challenge_baseline = lowest_skill_baseline(engine, challenge_employee, challenge_all)
    challenge_top = challenge_all[0]

    return {
        "evaluation_scope": {
            "dataset": "bundled synthetic dataset (fixed random seeds)",
            "employees": len(engine.employees),
            "eligible_employees": len(eligible),
            "events": len(engine.events),
            "recommendations_evaluated": len(all_recommendations),
            "limitations": (
                "Offline engineering checks only; business impact, fairness and promotion outcomes "
                "must be validated on governed real-world data before production use."
            ),
        },
        "metrics": {
            "recommendation_coverage_pct": pct(covered, len(eligible)),
            "deterministic_repeatability_pct": pct(stable, len(eligible)),
            "role_eligibility_pct": pct(role_eligible, len(all_recommendations)),
            "positive_counterfactual_pct": pct(positive_counterfactual, len(all_recommendations)),
            "explanation_completeness_pct": pct(explanation_complete, len(all_recommendations)),
            "lowest_skill_baseline_comparisons": len(baseline_pairs),
            "top_differs_from_lowest_skill_baseline_pct": pct(different_from_baseline, len(baseline_pairs)),
            "mean_ranking_score_advantage_points": round(mean(score_advantages), 2) if score_advantages else 0.0,
            "mean_top_readiness_delta_points": round(mean(top_readiness_deltas), 2) if top_readiness_deltas else 0.0,
            "mean_baseline_readiness_delta_points": (
                round(mean(baseline_readiness_deltas), 2) if baseline_readiness_deltas else 0.0
            ),
        },
        "jury_challenge_E0028": {
            "lowest_current_skill": min(challenge["skills"], key=lambda item: item["current"])["name"],
            "lowest_skill_baseline_event": challenge_baseline["title"] if challenge_baseline else None,
            "career_quest_top_event": challenge_top["title"],
            "career_quest_top_score": challenge_top["score"],
            "readiness_before": challenge_top["current_readiness"],
            "readiness_after": challenge_top["projected_readiness"],
        },
        "interpretation": {
            "coverage": "Share of eligible employees receiving at least one next step.",
            "repeatability": "Two fresh engines return the same ordered event IDs and scores.",
            "role_eligibility": "Every recommendation satisfies the event audience rule.",
            "positive_counterfactual": "Projected readiness is higher than current readiness.",
            "explanation_completeness": "Every recommendation exposes all four factors and score components.",
            "baseline": (
                "The baseline picks the highest-ranked available activity touching the employee's "
                "lowest absolute target-grade skill."
            ),
        },
    }


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2, sort_keys=True))
