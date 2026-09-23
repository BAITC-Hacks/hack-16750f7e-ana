"""Explainable multi-factor recommendation engine for Career Quest.

The engine deliberately keeps ranking deterministic. An LLM may verbalise the
facts in production, but it never invents the score or changes skill levels.
"""

from __future__ import annotations

import csv
import io
import random
import re
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any


GRADE_ORDER = ["Junior", "Middle", "Senior", "Lead"]
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
EVENT_TYPES = {"workshop", "mentoring", "course", "project", "assessment", "rotation", "activity"}
HISTORY_STATUSES = {"completed", "skipped", "declined", "missed"}


# These paths are product hypotheses for the synthetic demo, not a statement
# about Halyk Bank's current organisational structure. They are deliberately
# explicit so that a business owner can validate or replace every stage.
CAREER_TRACKS = [
    {
        "track_id": "retail_branch",
        "label": "Розничная сеть",
        "status": "hypothesis_for_business_validation",
        "path": [
            "Операционист",
            "Менеджер по обслуживанию",
            "Кредитный эксперт",
            "Руководитель отделения",
        ],
    },
    {
        "track_id": "sme",
        "label": "Малый и средний бизнес (МСБ)",
        "status": "hypothesis_for_business_validation",
        "path": [
            "Менеджер по работе с МСБ",
            "Старший менеджер МСБ",
            "Кредитный эксперт МСБ",
            "Руководитель направления МСБ",
        ],
    },
]


RETENTION_FEATURES = [
    "readiness",
    "participation_rate",
    "tenure_months",
    "has_next_step",
    "recent_missed_declined_or_skipped",
]


def next_grade(grade: str) -> str:
    try:
        index = GRADE_ORDER.index(grade)
    except ValueError:
        return "Senior"
    return GRADE_ORDER[min(index + 1, len(GRADE_ORDER) - 1)]


def is_terminal_grade(grade: str) -> bool:
    return grade == GRADE_ORDER[-1]


def local_text(value: Any, language: str = "ru") -> str:
    if isinstance(value, dict):
        return str(value.get(language) or value.get("en") or next(iter(value.values()), ""))
    return str(value or "")


SKILL_DEFINITIONS = [
    ("SK_PYTHON", "Python", "hard"),
    ("SK_SYSTEM_DESIGN", "System Design", "hard"),
    ("SK_PUBLIC_SPEAKING", "Публичные выступления", "soft"),
    ("SK_SQL", "SQL", "hard"),
    ("SK_DATA_VIS", "Визуализация данных", "hard"),
    ("SK_DATA_STORY", "Data storytelling", "soft"),
    ("SK_PRODUCT_STRATEGY", "Продуктовая стратегия", "hard"),
    ("SK_CUSTOMER_RESEARCH", "Исследование клиентов", "hard"),
    ("SK_STAKEHOLDER", "Работа со стейкхолдерами", "soft"),
    ("SK_LEADERSHIP", "Лидерство", "soft"),
    ("SK_COMMUNICATION", "Коммуникация", "soft"),
    ("SK_RISK", "Управление рисками", "hard"),
]


ROLE_SKILLS = {
    "Backend Engineer": ["SK_PYTHON", "SK_SYSTEM_DESIGN", "SK_PUBLIC_SPEAKING", "SK_COMMUNICATION"],
    "Data Analyst": ["SK_SQL", "SK_DATA_VIS", "SK_DATA_STORY", "SK_COMMUNICATION"],
    "Product Manager": ["SK_PRODUCT_STRATEGY", "SK_CUSTOMER_RESEARCH", "SK_STAKEHOLDER", "SK_PUBLIC_SPEAKING"],
    "Contact Center Specialist": ["SK_COMMUNICATION", "SK_STAKEHOLDER", "SK_RISK", "SK_LEADERSHIP"],
}


ROLE_LABELS = {
    "Backend Engineer": "Backend-разработчик",
    "Data Analyst": "Аналитик данных",
    "Product Manager": "Продакт-менеджер",
    "Contact Center Specialist": "Специалист контакт-центра",
}


def build_skills() -> list[dict[str, Any]]:
    skills = []
    for skill_id, name, kind in SKILL_DEFINITIONS:
        requirements: dict[str, dict[str, int]] = {}
        for role, role_skills in ROLE_SKILLS.items():
            if skill_id in role_skills:
                position = role_skills.index(skill_id)
                requirements[role] = {
                    "Junior": 1 if position > 1 else 2,
                    "Middle": 2 if position > 1 else 3,
                    "Senior": 3 if position > 1 else 4,
                    "Lead": 4 if position > 1 else 5,
                }
                # The verification profile from the brief already meets the
                # Senior Python bar; System Design is the critical gap.
                if role == "Backend Engineer" and skill_id == "SK_PYTHON":
                    requirements[role] = {"Junior": 2, "Middle": 3, "Senior": 3, "Lead": 4}
        skills.append({"skill_id": skill_id, "name": {"ru": name, "en": name}, "type": kind, "requirements": requirements})
    return skills


def build_events() -> list[dict[str, Any]]:
    raw = [
        ("EV_SYSTEM_DESIGN", "Практикум по System Design", "workshop", ["Backend Engineer"], [("SK_SYSTEM_DESIGN", 1, 4)], 6),
        ("EV_ARCH_MENTOR", "Разбор архитектуры с ментором", "mentoring", ["Backend Engineer"], [("SK_SYSTEM_DESIGN", 1, 5), ("SK_COMMUNICATION", 1, 4)], 3),
        ("EV_PYTHON_ADV", "Продвинутый Python", "course", ["Backend Engineer"], [("SK_PYTHON", 1, 5)], 8),
        ("EV_PUBLIC_SPEAKING", "Уверенное публичное выступление", "workshop", [], [("SK_PUBLIC_SPEAKING", 1, 4)], 5),
        ("EV_SQL_CASE", "SQL на банковских данных", "project", ["Data Analyst"], [("SK_SQL", 1, 5)], 6),
        ("EV_DASHBOARD", "Дашборд для бизнес-заказчика", "project", ["Data Analyst"], [("SK_DATA_VIS", 1, 5), ("SK_DATA_STORY", 1, 4)], 8),
        ("EV_STORY_MENTOR", "Data storytelling с наставником", "mentoring", ["Data Analyst"], [("SK_DATA_STORY", 1, 5)], 3),
        ("EV_DISCOVERY", "Customer discovery sprint", "project", ["Product Manager"], [("SK_CUSTOMER_RESEARCH", 1, 5)], 7),
        ("EV_PRODUCT_CASE", "Защита продуктовой стратегии", "assessment", ["Product Manager"], [("SK_PRODUCT_STRATEGY", 1, 5), ("SK_PUBLIC_SPEAKING", 1, 4)], 5),
        ("EV_STAKEHOLDER", "Переговоры со стейкхолдерами", "workshop", ["Product Manager", "Contact Center Specialist"], [("SK_STAKEHOLDER", 1, 5)], 4),
        ("EV_TEAM_LEAD", "Теневая смена руководителя", "rotation", ["Contact Center Specialist"], [("SK_LEADERSHIP", 1, 4), ("SK_RISK", 1, 4)], 8),
        ("EV_FEEDBACK", "Практика развивающей обратной связи", "mentoring", [], [("SK_COMMUNICATION", 1, 5), ("SK_LEADERSHIP", 1, 4)], 3),
    ]
    return [
        {
            "event_id": event_id,
            "title": {"ru": title, "en": title},
            "type": event_type,
            "audience": {"roles": roles},
            "skills": [{"skill_id": skill, "gain": gain, "max_level": cap} for skill, gain, cap in gains],
            "duration_hours": hours,
            "cooldown_days": 90,
            "voluntary": True,
        }
        for event_id, title, event_type, roles, gains, hours in raw
    ]


def build_employees(count: int = 200) -> list[dict[str, Any]]:
    rng = random.Random(42)
    first_names = ["Аян", "Дана", "Айгерим", "Нурлан", "Мадина", "Алихан", "Аружан", "Тимур"]
    last_names = ["Серик", "Омарова", "Ибраев", "Садыкова", "Ким", "Ахметов"]
    roles = list(ROLE_SKILLS)
    employees = []
    for index in range(1, count + 1):
        role = roles[(index - 1) % len(roles)]
        grade = "Middle" if index % 4 else "Junior"
        role_skills = ROLE_SKILLS[role]
        levels = {skill_id: rng.randint(1, 3 if grade == "Middle" else 2) for skill_id in role_skills}
        employees.append(
            {
                "employee_id": f"E{index:04d}",
                "name": f"{first_names[index % len(first_names)]} {last_names[index % len(last_names)]}",
                "role": role,
                "grade": grade,
                "tenure_months": rng.randint(12, 60),
                "skills": levels,
            }
        )

    employees[27] = {
        "employee_id": "E0028",
        "name": "Аян Серик",
        "role": "Backend Engineer",
        "grade": "Middle",
        "tenure_months": 52,
        "skills": {"SK_PYTHON": 3, "SK_SYSTEM_DESIGN": 2, "SK_PUBLIC_SPEAKING": 1, "SK_COMMUNICATION": 3},
    }
    return employees


def build_history(employees: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rng = random.Random(73)
    history: list[dict[str, Any]] = []
    today = date.today()
    for employee in employees:
        suitable = [e for e in events if not e["audience"]["roles"] or employee["role"] in e["audience"]["roles"]]
        for offset in range(rng.randint(4, 9)):
            event = suitable[(offset + int(employee["employee_id"][1:])) % len(suitable)]
            status = rng.choices(["completed", "skipped", "declined"], weights=[7, 2, 1])[0]
            history.append(
                {
                    "employee_id": employee["employee_id"],
                    "event_id": event["event_id"],
                    "status": status,
                    "on_time": status == "completed" and rng.random() > 0.18,
                    "date": str(today - timedelta(days=30 * (offset + 1))),
                }
            )

    history = [row for row in history if row["employee_id"] != "E0028"]
    history.extend(
        [
            {"employee_id": "E0028", "event_id": "EV_PUBLIC_SPEAKING", "status": "skipped", "on_time": False, "date": "2026-03-12"},
            {"employee_id": "E0028", "event_id": "EV_PUBLIC_SPEAKING", "status": "declined", "on_time": False, "date": "2026-05-20"},
            {"employee_id": "E0028", "event_id": "EV_PUBLIC_SPEAKING", "status": "skipped", "on_time": False, "date": "2026-07-11"},
            {"employee_id": "E0028", "event_id": "EV_PYTHON_ADV", "status": "completed", "on_time": True, "date": "2026-04-05"},
            {"employee_id": "E0028", "event_id": "EV_ARCH_MENTOR", "status": "completed", "on_time": True, "date": "2026-08-18"},
        ]
    )
    return history


@dataclass
class ScoreParts:
    grade_relevance: float
    trajectory_impact: float
    history_fit: float
    feasibility: float

    @property
    def total(self) -> float:
        return round(
            100
            * (
                0.46 * self.grade_relevance
                + 0.26 * self.trajectory_impact
                + 0.18 * self.history_fit
                + 0.10 * self.feasibility
            ),
            1,
        )


class CareerEngine:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.skills = build_skills()
        self.events = build_events()
        self.employees = build_employees()
        self.history = build_history(self.employees, self.events)
        self.data_source = "Встроенный демо-набор"

    def employee(self, employee_id: str) -> dict[str, Any]:
        for employee in self.employees:
            if employee.get("employee_id") == employee_id:
                return employee
        raise KeyError(f"Сотрудник {employee_id} не найден")

    def event(self, event_id: str) -> dict[str, Any] | None:
        return next((item for item in self.events if item.get("event_id") == event_id), None)

    def skill(self, skill_id: str) -> dict[str, Any] | None:
        return next((item for item in self.skills if item.get("skill_id") == skill_id), None)

    def requirements(self, employee: dict[str, Any], target: str) -> dict[str, int]:
        requirements: dict[str, int] = {}
        for skill in self.skills:
            role_requirements = skill.get("requirements", {}).get(employee.get("role"), {})
            if target in role_requirements:
                requirements[skill["skill_id"]] = int(role_requirements[target])
                continue
            # Per-skill fallback for simpler starter-kit schemas. Mixed
            # catalogs may contain role-specific and generic definitions.
            levels = skill.get("grade_requirements") or skill.get("requirements_by_grade") or {}
            value = levels.get(target)
            if value is not None:
                requirements[skill["skill_id"]] = int(value)
        return requirements

    @staticmethod
    def readiness(levels: dict[str, int], requirements: dict[str, int]) -> float:
        if not requirements:
            return 100.0
        covered = sum(
            1.0 if required <= 0 else min(float(levels.get(skill_id, 0)) / required, 1.0)
            for skill_id, required in requirements.items()
        )
        return round(covered / len(requirements) * 100, 1)

    def _history_stats(self, employee_id: str, event: dict[str, Any]) -> dict[str, Any]:
        rows = [row for row in self.history if row.get("employee_id") == employee_id]
        same_event = [row for row in rows if row.get("event_id") == event.get("event_id")]
        type_ids = {item["event_id"] for item in self.events if item.get("type") == event.get("type")}
        same_type = [row for row in rows if row.get("event_id") in type_ids]
        completed = sum(row.get("status") == "completed" for row in same_type)
        on_time = sum(row.get("status") == "completed" and bool(row.get("on_time", True)) for row in same_type)
        misses = sum(row.get("status") in {"skipped", "declined", "missed"} for row in same_event)
        denominator = max(len(same_type), 1)
        completion_rate = completed / denominator
        on_time_rate = on_time / max(completed, 1)
        fit = 0.45 + 0.35 * completion_rate + 0.20 * on_time_rate - 0.18 * misses
        return {
            "completed": completed,
            "on_time": on_time,
            "misses": misses,
            "fit": max(0.05, min(fit, 1.0)),
        }

    def _completed_within_cooldown(self, employee_id: str, event: dict[str, Any]) -> bool:
        """Keep a completed step out of the route for a bounded period.

        Catalogue entries describe formats that may run every quarter. Treating
        one historical completion as a lifetime ban produced false dead ends;
        the default 90-day cooldown preserves visible route progression while
        allowing purposeful reinforcement later.
        """

        cooldown_days = int(event.get("cooldown_days", 90))
        today = date.today()
        for row in self.history:
            if (
                row.get("employee_id") != employee_id
                or row.get("event_id") != event.get("event_id")
                or row.get("status") != "completed"
            ):
                continue
            raw_date = row.get("date")
            if not isinstance(raw_date, str):
                return True
            try:
                completed_on = date.fromisoformat(raw_date)
            except ValueError:
                return True
            if (today - completed_on).days < cooldown_days:
                return True
        return False

    def recommendations(self, employee_id: str, limit: int = 3) -> list[dict[str, Any]]:
        employee = self.employee(employee_id)
        if is_terminal_grade(str(employee.get("grade", "Middle"))):
            return []
        target = next_grade(str(employee.get("grade", "Middle")))
        requirements = self.requirements(employee, target)
        current_levels = {key: int(value) for key, value in employee.get("skills", {}).items()}
        current_readiness = self.readiness(current_levels, requirements)
        gaps = {skill_id: max(required - current_levels.get(skill_id, 0), 0) for skill_id, required in requirements.items()}
        ranked: list[dict[str, Any]] = []

        for event in self.events:
            if self._completed_within_cooldown(employee_id, event):
                continue
            roles = event.get("audience", {}).get("roles", []) if isinstance(event.get("audience"), dict) else []
            if roles and employee.get("role") not in roles:
                continue
            gains = event.get("skills") or event.get("skill_gains") or []
            affected = []
            simulated = dict(current_levels)
            relevance_values = []
            for gain_item in gains:
                skill_id = gain_item.get("skill_id") or gain_item.get("skill")
                if not skill_id or gaps.get(skill_id, 0) <= 0:
                    continue
                gain = int(gain_item.get("gain", 1))
                cap = int(gain_item.get("max_level", 5))
                before = simulated.get(skill_id, 0)
                after = min(before + gain, cap)
                effective_gain = max(after - before, 0)
                if effective_gain == 0:
                    continue
                required = requirements[skill_id]
                gap = gaps[skill_id]
                severity = gap / max(required, 1)
                importance = required / 5
                coverage = min(effective_gain / max(gap, 1), 1.0)
                # Large, grade-critical gaps must outrank a cosmetically low
                # skill that is easier to close. Coverage is useful, but it
                # must not collapse the recommendation to "pick the minimum".
                relevance_values.append(0.55 * severity + 0.30 * importance + 0.15 * coverage)
                simulated[skill_id] = after
                skill_info = self.skill(skill_id) or {"name": skill_id}
                affected.append(
                    {
                        "skill_id": skill_id,
                        "name": local_text(skill_info.get("name")),
                        "before": before,
                        "after": after,
                        "required": required,
                        "gap_before": gap,
                    }
                )
            if not affected:
                continue

            projected = self.readiness(simulated, requirements)
            delta = round(projected - current_readiness, 1)
            history = self._history_stats(employee_id, event)
            grade_relevance = min(sum(relevance_values) / max(len(requirements), 1) * 3.2, 1.0)
            impact = min(delta / 14.0, 1.0)
            duration = float(event.get("duration_hours", 4) or 4)
            feasibility = max(0.35, min(1.0, 1.12 - duration / 20))
            parts = ScoreParts(grade_relevance, impact, history["fit"], feasibility)
            primary = affected[0]

            if history["completed"]:
                history_fact = f"Ранее завершено похожих активностей: {history['completed']}, вовремя: {history['on_time']}."
            elif history["misses"]:
                history_fact = f"Похожие активности пропускались {history['misses']} раз, поэтому рекомендация понижена в рейтинге."
            else:
                history_fact = "В истории нет повторных отказов от такого формата."

            ranked.append(
                {
                    "event_id": event["event_id"],
                    "title": local_text(event.get("title")),
                    "type": event.get("type", "activity"),
                    "duration_hours": duration,
                    "score": parts.total,
                    "current_readiness": current_readiness,
                    "projected_readiness": projected,
                    "readiness_delta": delta,
                    "affected_skills": affected,
                    "factors": [
                        {
                            "key": "grade",
                            "label": "Требование грейда",
                            "text": f"Для {target} навык «{primary['name']}» нужен на уровне {primary['required']}, сейчас {primary['before']}.",
                        },
                        {
                            "key": "impact",
                            "label": "Эффект",
                            "text": f"Активность повышает готовность к грейду с {current_readiness}% до {projected}%." ,
                        },
                        {"key": "history", "label": "История участия", "text": history_fact},
                        {
                            "key": "format",
                            "label": "Реалистичность",
                            "text": (
                                f"{'Добровольный формат' if event.get('voluntary', True) else 'Обязательная активность'}, "
                                f"нагрузка около {duration:g} ч.; учитывается при оценке выполнимости."
                            ),
                        },
                    ],
                    "score_breakdown": {
                        "grade_relevance": round(parts.grade_relevance * 100),
                        "trajectory_impact": round(parts.trajectory_impact * 100),
                        "history_fit": round(parts.history_fit * 100),
                        "feasibility": round(parts.feasibility * 100),
                    },
                }
            )

        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked[:limit]

    def employee_view(self, employee_id: str) -> dict[str, Any]:
        source = self.employee(employee_id)
        # Return an explicit DTO instead of reflecting the whole uploaded
        # record.  Fields such as salary, phone or auth_role must never leak.
        employee = {
            "employee_id": str(source.get("employee_id", "")),
            "name": str(source.get("name") or source.get("employee_id", "")),
            "role": str(source.get("role", "")),
            "grade": str(source.get("grade", "Middle")),
            "tenure_months": int(source.get("tenure_months", 0) or 0),
            "skills": {str(key): int(value) for key, value in dict(source.get("skills", {})).items()},
        }
        terminal = is_terminal_grade(str(employee.get("grade", "Middle")))
        target = next_grade(str(employee.get("grade", "Middle")))
        requirements = {} if terminal else self.requirements(employee, target)
        levels = {key: int(value) for key, value in employee.get("skills", {}).items()}
        skill_rows = []
        for skill_id, required in requirements.items():
            info = self.skill(skill_id) or {"name": skill_id, "type": "hard"}
            current = levels.get(skill_id, 0)
            skill_rows.append(
                {
                    "skill_id": skill_id,
                    "name": local_text(info.get("name")),
                    "type": info.get("type", "hard"),
                    "current": current,
                    "required": required,
                    "gap": max(required - current, 0),
                }
            )
        skill_rows.sort(key=lambda row: (row["gap"], row["required"]), reverse=True)
        recommendations = self.recommendations(employee_id)
        lowest = min(skill_rows, key=lambda row: row["current"], default=None)
        top_skills = {skill["skill_id"] for rec in recommendations[:1] for skill in rec["affected_skills"]}
        insight = None
        if lowest and lowest["skill_id"] not in top_skills and recommendations:
            insight = (
                f"Самый низкий навык — «{lowest['name']}», но один только минимум не определяет маршрут. "
                f"«{recommendations[0]['affected_skills'][0]['name']}» получает лучший общий ranking score после учёта "
                f"требований {target}, прогнозируемого эффекта, истории формата и нагрузки. "
                "Повторные пропуски трактуются только как сигнал подобрать реалистичный формат, а не как оценка мотивации."
            )
        history_rows = []
        employee_history = sorted(
            (row for row in self.history if row.get("employee_id") == employee_id),
            key=lambda row: str(row.get("date", "")),
            reverse=True,
        )[:8]
        for row in employee_history:
            event = self.event(str(row.get("event_id"))) or {}
            history_rows.append({**row, "title": local_text(event.get("title") or row.get("event_id"))})
        return {
            "employee": employee,
            "role_label": ROLE_LABELS.get(str(employee.get("role")), str(employee.get("role"))),
            "target_grade": "Экспертный трек" if terminal else target,
            "terminal_grade": terminal,
            "readiness": self.readiness(levels, requirements),
            "skills": skill_rows,
            "recommendations": recommendations,
            "history": history_rows,
            "decision_insight": insight,
            "data_source": self.data_source,
        }

    def complete(self, employee_id: str, event_id: str) -> dict[str, Any]:
        employee = self.employee(employee_id)
        event = self.event(event_id)
        if not event:
            raise KeyError(f"Активность {event_id} не найдена")
        roles = event.get("audience", {}).get("roles", []) if isinstance(event.get("audience"), dict) else []
        if roles and employee.get("role") not in roles:
            raise ValueError("Эта активность недоступна для роли сотрудника")
        if self._completed_within_cooldown(employee_id, event):
            raise ValueError("Активность уже была завершена в пределах cooldown")
        recommended_ids = {item["event_id"] for item in self.recommendations(employee_id, limit=len(self.events))}
        if event_id not in recommended_ids:
            raise ValueError("Активность не относится к текущей траектории развития")
        for item in event.get("skills", []):
            skill_id = item.get("skill_id")
            if not skill_id:
                continue
            before = int(employee.setdefault("skills", {}).get(skill_id, 0))
            employee["skills"][skill_id] = max(
                before,
                min(before + int(item.get("gain", 1)), int(item.get("max_level", 5))),
            )
        self.history.append(
            {"employee_id": employee_id, "event_id": event_id, "status": "completed", "on_time": True, "date": str(date.today())}
        )
        return self.employee_view(employee_id)

    @staticmethod
    def _retention_case(
        employee: dict[str, Any],
        readiness: float,
        participation: float,
        has_next_step: bool,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build an explainable support indicator from non-protected fields.

        This is intentionally a transparent heuristic for prioritising
        voluntary manager check-ins. It is not an attrition prediction and
        must not automate employment, compensation or promotion decisions.
        """

        score = 0
        signals: list[dict[str, Any]] = []
        tenure = int(employee.get("tenure_months", 0) or 0)
        cutoff = date.today() - timedelta(days=180)
        recent_non_completions = 0
        for row in rows:
            if row.get("status") not in {"missed", "declined", "skipped"}:
                continue
            try:
                happened_on = date.fromisoformat(str(row.get("date", "")))
            except ValueError:
                continue
            if happened_on >= cutoff:
                recent_non_completions += 1

        def add_signal(key: str, points: int, evidence: str) -> None:
            nonlocal score
            score += points
            signals.append({"key": key, "points": points, "evidence": evidence})

        if not has_next_step:
            add_signal("no_next_step", 25, "Для сотрудника не найден следующий развивающий шаг в текущем каталоге.")
        if tenure >= 36:
            add_signal("long_tenure", 18, f"Стаж в компании — {tenure} мес.; стоит проверить ожидания роста.")
        elif tenure >= 24:
            add_signal("tenure", 10, f"Стаж в компании — {tenure} мес.; уместен плановый карьерный диалог.")
        if readiness >= 80 and tenure >= 24:
            add_signal(
                "ready_without_movement",
                16,
                f"Готовность {readiness:g}% при стаже {tenure} мес. может означать потребность в следующем шаге.",
            )
        elif readiness < 55:
            add_signal("readiness_support", 10, f"Готовность {readiness:g}%: может потребоваться наставник или иной формат развития.")
        if participation < 50:
            add_signal(
                "low_participation",
                15,
                f"Завершено {participation:g}% назначенных активностей; это сигнал проверить нагрузку и доступность формата.",
            )
        elif participation < 70:
            add_signal(
                "participation",
                8,
                f"Завершено {participation:g}% активностей; полезно уточнить, какой формат сотруднику реалистичен.",
            )
        if recent_non_completions:
            points = min(20, recent_non_completions * 7)
            add_signal(
                "recent_non_completion",
                points,
                f"За последние 180 дней: {recent_non_completions} пропусков или отказов; причина не интерпретируется автоматически.",
            )

        score = min(score, 100)
        risk_band = "high" if score >= 55 else "medium" if score >= 30 else "low"
        signal_keys = {signal["key"] for signal in signals}
        if "ready_without_movement" in signal_keys or "no_next_step" in signal_keys:
            action = "Провести добровольный карьерный диалог и вместе выбрать следующий шаг, ротацию или экспертный трек."
        elif "recent_non_completion" in signal_keys or "low_participation" in signal_keys:
            action = "Уточнить барьеры без оценки мотивации и предложить другой формат, срок или наставника."
        elif "readiness_support" in signal_keys:
            action = "Согласовать короткий план закрытия ключевого gap и контрольную точку с наставником."
        else:
            action = "Сохранить плановый check-in; срочное вмешательство по этому индикатору не требуется."
        return {
            "employee_id": str(employee.get("employee_id", "")),
            "name": str(employee.get("name") or employee.get("employee_id", "")),
            "role": ROLE_LABELS.get(str(employee.get("role", "")), str(employee.get("role", ""))),
            "risk_band": risk_band,
            "indicator_score": score,
            "feature_snapshot": {
                "readiness": readiness,
                "participation_rate": round(participation, 1),
                "tenure_months": tenure,
                "has_next_step": has_next_step,
                "recent_missed_declined_or_skipped": recent_non_completions,
            },
            "signals": signals,
            "suggested_next_action": action,
        }

    def hr_view(self) -> dict[str, Any]:
        gaps: Counter[str] = Counter()
        participants = Counter(row.get("status", "unknown") for row in self.history)
        watchlist = []
        without_step = 0
        readiness_values = []
        retention_cases = []
        for employee in self.employees:
            terminal = is_terminal_grade(str(employee.get("grade", "Middle")))
            target = next_grade(str(employee.get("grade", "Middle")))
            requirements = {} if terminal else self.requirements(employee, target)
            levels = employee.get("skills", {})
            readiness = self.readiness(levels, requirements)
            readiness_values.append(readiness)
            for skill_id, required in requirements.items():
                gaps[skill_id] += max(int(required) - int(levels.get(skill_id, 0)), 0)
            recommendations = self.recommendations(employee["employee_id"])
            if not recommendations and not terminal:
                without_step += 1
            rows = [row for row in self.history if row.get("employee_id") == employee["employee_id"]]
            participation = sum(row.get("status") == "completed" for row in rows) / max(len(rows), 1) * 100
            if readiness < 58 and participation < 55:
                watchlist.append(
                    {
                        "employee_id": employee["employee_id"],
                        "name": employee.get("name", employee["employee_id"]),
                        "role": ROLE_LABELS.get(employee.get("role"), employee.get("role")),
                        "readiness": readiness,
                        "participation": round(participation),
                    }
                )
            retention_cases.append(
                self._retention_case(
                    employee,
                    readiness,
                    participation,
                    bool(recommendations) or terminal,
                    rows,
                )
            )
        gap_rows = []
        for skill_id, total_gap in gaps.most_common(7):
            skill = self.skill(skill_id) or {"name": skill_id}
            gap_rows.append({"skill_id": skill_id, "name": local_text(skill.get("name")), "total_gap": total_gap})
        total_history = max(sum(participants.values()), 1)
        retention_counts = Counter(case["risk_band"] for case in retention_cases)
        retention_cases.sort(key=lambda case: (-case["indicator_score"], case["employee_id"]))
        return {
            "employees": len(self.employees),
            "average_readiness": round(sum(readiness_values) / max(len(readiness_values), 1), 1),
            "without_step": without_step,
            "completion_rate": round(participants["completed"] / total_history * 100, 1),
            "skill_gaps": gap_rows,
            "participation": [{"status": key, "count": value, "percent": round(value / total_history * 100, 1)} for key, value in participants.items()],
            "watchlist": sorted(watchlist, key=lambda row: row["readiness"])[:8],
            "career_tracks": deepcopy(CAREER_TRACKS),
            "retention_summary": {
                "total_evaluated": len(retention_cases),
                "low": retention_counts["low"],
                "medium": retention_counts["medium"],
                "high": retention_counts["high"],
                "high_risk_count": retention_counts["high"],
                "model_type": "transparent_synthetic_heuristic",
                "features_used": list(RETENTION_FEATURES),
                "protected_attributes_used": [],
                "decision_policy": (
                    "Только приоритизация добровольного разговора с менеджером; "
                    "не автоматическое кадровое, зарплатное или promotion-решение."
                ),
            },
            "retention_cases": retention_cases[:8],
            "data_source": self.data_source,
        }

    def upload_bundle(self, bundle: dict[str, Any]) -> dict[str, Any]:
        employees = bundle.get("employees") or bundle.get("profiles")
        events = bundle.get("events")
        skills = bundle.get("skills")
        history = bundle.get("history") or bundle.get("activity_history")
        if isinstance(employees, dict):
            employees = [employees]
        if employees:
            normalized = []
            for index, item in enumerate(employees):
                record = dict(item)
                record.setdefault("employee_id", f"UP{index + 1:04d}")
                record.setdefault("name", record["employee_id"])
                record.setdefault("grade", "Middle")
                record.setdefault("skills", {})
                normalized.append(record)
            known = {item["employee_id"] for item in normalized}
            self.employees = [item for item in self.employees if item["employee_id"] not in known] + normalized
        if events:
            self.events = list(events)
        if skills:
            self.skills = list(skills.values()) if isinstance(skills, dict) else list(skills)
        if history:
            uploaded_ids = {item.get("employee_id") for item in history}
            self.history = [row for row in self.history if row.get("employee_id") not in uploaded_ids] + list(history)
        self.data_source = "Загруженный набор жюри"
        return {
            "employees": len(self.employees),
            "events": len(self.events),
            "skills": len(self.skills),
            "history": len(self.history),
            "data_source": self.data_source,
        }

    @staticmethod
    def _text(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
        if not isinstance(value, str):
            raise ValueError(f"Поле {field} должно быть строкой")
        result = value.strip()
        if not allow_empty and not result:
            raise ValueError(f"Поле {field} не может быть пустым")
        if len(result) > maximum:
            raise ValueError(f"Поле {field} длиннее {maximum} символов")
        return result

    @classmethod
    def _identifier(cls, value: Any, field: str) -> str:
        result = cls._text(value, field, 80)
        if not SAFE_ID.fullmatch(result):
            raise ValueError(f"Поле {field} содержит недопустимый идентификатор")
        return result

    @staticmethod
    def _integer(value: Any, field: str, low: int, high: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Поле {field} должно быть целым числом")
        if not low <= value <= high:
            raise ValueError(f"Поле {field} должно быть от {low} до {high}")
        return value

    @classmethod
    def _localized_name(cls, value: Any, field: str) -> str | dict[str, str]:
        if isinstance(value, str):
            return cls._text(value, field, 160)
        if not isinstance(value, dict) or not value or len(value) > 5:
            raise ValueError(f"Поле {field} должно быть строкой или объектом локализаций")
        result: dict[str, str] = {}
        for language, text in value.items():
            code = cls._text(language, f"{field}.language", 12)
            result[code] = cls._text(text, f"{field}.{code}", 160)
        return result

    @classmethod
    def _validate_bundle(cls, bundle: dict[str, Any], current: "CareerEngine") -> dict[str, Any]:
        supported = {"employees", "profiles", "events", "skills", "history", "activity_history"}
        unknown = set(bundle) - supported
        if unknown:
            raise ValueError(f"Неизвестные разделы набора: {', '.join(sorted(unknown))}")
        if "employees" in bundle and "profiles" in bundle:
            raise ValueError("Используйте только один раздел: employees или profiles")
        if "history" in bundle and "activity_history" in bundle:
            raise ValueError("Используйте только один раздел: history или activity_history")
        if not bundle or not any(bundle.get(key) for key in supported):
            raise ValueError("Набор данных пуст")

        cleaned: dict[str, Any] = {}
        raw_employees = bundle.get("employees") or bundle.get("profiles")
        if raw_employees is not None:
            if isinstance(raw_employees, dict):
                raw_employees = [raw_employees]
            if not isinstance(raw_employees, list) or not 1 <= len(raw_employees) <= 500:
                raise ValueError("employees должен содержать от 1 до 500 записей")
            employees: list[dict[str, Any]] = []
            seen: set[str] = set()
            for index, item in enumerate(raw_employees):
                if not isinstance(item, dict):
                    raise ValueError(f"employees[{index}] должен быть объектом")
                employee_id = cls._identifier(item.get("employee_id"), f"employees[{index}].employee_id")
                if employee_id in seen:
                    raise ValueError(f"Дублирующий employee_id: {employee_id}")
                seen.add(employee_id)
                grade = cls._text(item.get("grade", "Middle"), f"employees[{index}].grade", 40)
                if grade not in GRADE_ORDER:
                    raise ValueError(f"Неизвестный grade: {grade}")
                skills = item.get("skills", {})
                if not isinstance(skills, dict) or len(skills) > 100:
                    raise ValueError(f"employees[{index}].skills должен быть объектом до 100 навыков")
                normalized_skills = {
                    cls._identifier(skill_id, f"employees[{index}].skills.id"): cls._integer(
                        level, f"employees[{index}].skills.{skill_id}", 0, 5
                    )
                    for skill_id, level in skills.items()
                }
                employees.append(
                    {
                        "employee_id": employee_id,
                        "name": cls._text(item.get("name", employee_id), f"employees[{index}].name", 160),
                        "role": cls._text(item.get("role", ""), f"employees[{index}].role", 80),
                        "grade": grade,
                        "tenure_months": cls._integer(item.get("tenure_months", 0), f"employees[{index}].tenure_months", 0, 600),
                        "skills": normalized_skills,
                    }
                )
            cleaned["employees"] = employees

        raw_skills = bundle.get("skills")
        if raw_skills is not None:
            if isinstance(raw_skills, dict):
                expanded = []
                for skill_key, skill_value in raw_skills.items():
                    if not isinstance(skill_value, dict):
                        raise ValueError("Каждый навык должен быть объектом")
                    expanded.append({"skill_id": skill_key, **skill_value})
                raw_skills = expanded
            if not isinstance(raw_skills, list) or not 1 <= len(raw_skills) <= 250:
                raise ValueError("skills должен содержать от 1 до 250 записей")
            skills: list[dict[str, Any]] = []
            seen = set()
            for index, item in enumerate(raw_skills):
                if not isinstance(item, dict):
                    raise ValueError(f"skills[{index}] должен быть объектом")
                skill_id = cls._identifier(item.get("skill_id"), f"skills[{index}].skill_id")
                if skill_id in seen:
                    raise ValueError(f"Дублирующий skill_id: {skill_id}")
                seen.add(skill_id)
                kind = cls._text(item.get("type", "hard"), f"skills[{index}].type", 20)
                if kind not in {"hard", "soft"}:
                    raise ValueError(f"Недопустимый тип навыка: {kind}")
                requirements = item.get("requirements", {})
                if not isinstance(requirements, dict) or len(requirements) > 100:
                    raise ValueError(f"skills[{index}].requirements должен быть объектом")
                normalized_requirements: dict[str, dict[str, int]] = {}
                for role, levels in requirements.items():
                    role_name = cls._text(role, f"skills[{index}].requirements.role", 80)
                    if not isinstance(levels, dict):
                        raise ValueError(f"Требования роли {role_name} должны быть объектом")
                    normalized_levels: dict[str, int] = {}
                    for grade, level in levels.items():
                        grade_name = cls._text(grade, "requirements.grade", 40)
                        if grade_name not in GRADE_ORDER:
                            raise ValueError(f"Неизвестный grade в requirements: {grade_name}")
                        normalized_levels[grade_name] = cls._integer(level, "requirements.level", 1, 5)
                    normalized_requirements[role_name] = normalized_levels
                generic = item.get("grade_requirements") or item.get("requirements_by_grade")
                normalized_generic: dict[str, int] = {}
                if generic is not None:
                    if not isinstance(generic, dict):
                        raise ValueError(f"skills[{index}].grade_requirements должен быть объектом")
                    for grade, level in generic.items():
                        grade_name = cls._text(grade, "grade_requirements.grade", 40)
                        if grade_name not in GRADE_ORDER:
                            raise ValueError(f"Неизвестный grade в grade_requirements: {grade_name}")
                        normalized_generic[grade_name] = cls._integer(level, "grade_requirements.level", 1, 5)
                skill_record = {
                    "skill_id": skill_id,
                    "name": cls._localized_name(item.get("name", skill_id), f"skills[{index}].name"),
                    "type": kind,
                    "requirements": normalized_requirements,
                }
                if normalized_generic:
                    skill_record["grade_requirements"] = normalized_generic
                skills.append(skill_record)
            cleaned["skills"] = skills

        raw_events = bundle.get("events")
        if raw_events is not None:
            if not isinstance(raw_events, list) or not 1 <= len(raw_events) <= 150:
                raise ValueError("events должен содержать от 1 до 150 записей")
            events: list[dict[str, Any]] = []
            seen = set()
            for index, item in enumerate(raw_events):
                if not isinstance(item, dict):
                    raise ValueError(f"events[{index}] должен быть объектом")
                event_id = cls._identifier(item.get("event_id"), f"events[{index}].event_id")
                if event_id in seen:
                    raise ValueError(f"Дублирующий event_id: {event_id}")
                seen.add(event_id)
                event_type = cls._text(item.get("type", "activity"), f"events[{index}].type", 30)
                if event_type not in EVENT_TYPES:
                    raise ValueError(f"Недопустимый тип активности: {event_type}")
                audience = item.get("audience", {})
                if not isinstance(audience, dict):
                    raise ValueError(f"events[{index}].audience должен быть объектом")
                roles = audience.get("roles", [])
                if not isinstance(roles, list) or len(roles) > 50:
                    raise ValueError(f"events[{index}].audience.roles должен быть массивом")
                normalized_roles = [cls._text(role, "audience.role", 80) for role in roles]
                gains = item.get("skills") or item.get("skill_gains") or []
                if not isinstance(gains, list) or not 1 <= len(gains) <= 30:
                    raise ValueError(f"events[{index}].skills должен содержать от 1 до 30 элементов")
                normalized_gains = []
                seen_gain_skills: set[str] = set()
                for gain_index, gain in enumerate(gains):
                    if not isinstance(gain, dict):
                        raise ValueError(f"events[{index}].skills[{gain_index}] должен быть объектом")
                    gain_skill_id = cls._identifier(gain.get("skill_id") or gain.get("skill"), "event.skill_id")
                    if gain_skill_id in seen_gain_skills:
                        raise ValueError(f"Активность {event_id} повторяет skill_id: {gain_skill_id}")
                    seen_gain_skills.add(gain_skill_id)
                    normalized_gains.append(
                        {
                            "skill_id": gain_skill_id,
                            "gain": cls._integer(gain.get("gain", 1), "event.gain", 1, 5),
                            "max_level": cls._integer(gain.get("max_level", 5), "event.max_level", 1, 5),
                        }
                    )
                duration = item.get("duration_hours", 4)
                if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0.25 <= float(duration) <= 200:
                    raise ValueError(f"events[{index}].duration_hours должен быть от 0.25 до 200")
                cooldown_days = cls._integer(item.get("cooldown_days", 90), f"events[{index}].cooldown_days", 1, 730)
                voluntary = item.get("voluntary", True)
                if not isinstance(voluntary, bool):
                    raise ValueError(f"events[{index}].voluntary должен быть boolean")
                events.append(
                    {
                        "event_id": event_id,
                        "title": cls._localized_name(item.get("title", event_id), f"events[{index}].title"),
                        "type": event_type,
                        "audience": {"roles": normalized_roles},
                        "skills": normalized_gains,
                        "duration_hours": float(duration),
                        "cooldown_days": cooldown_days,
                        "voluntary": voluntary,
                    }
                )
            cleaned["events"] = events

        raw_history = bundle.get("history") or bundle.get("activity_history")
        if raw_history is not None:
            if not isinstance(raw_history, list) or not 1 <= len(raw_history) <= 10_000:
                raise ValueError("history должен содержать от 1 до 10000 записей")
            history: list[dict[str, Any]] = []
            for index, item in enumerate(raw_history):
                if not isinstance(item, dict):
                    raise ValueError(f"history[{index}] должен быть объектом")
                status = cls._text(item.get("status", ""), f"history[{index}].status", 20)
                if status not in HISTORY_STATUSES:
                    raise ValueError(f"Недопустимый status: {status}")
                on_time = item.get("on_time", False)
                if isinstance(on_time, str):
                    normalized_on_time = on_time.strip().lower()
                    if normalized_on_time in {"1", "true", "yes", "да"}:
                        on_time = True
                    elif normalized_on_time in {"0", "false", "no", "нет", ""}:
                        on_time = False
                    else:
                        raise ValueError(f"history[{index}].on_time содержит неизвестное значение")
                if not isinstance(on_time, bool):
                    raise ValueError(f"history[{index}].on_time должен быть boolean")
                date_value = cls._text(item.get("date", ""), f"history[{index}].date", 10)
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
                    raise ValueError(f"history[{index}].date должен быть YYYY-MM-DD")
                try:
                    parsed_date = date.fromisoformat(date_value)
                except ValueError as exc:
                    raise ValueError(f"history[{index}].date должен быть YYYY-MM-DD") from exc
                if str(parsed_date) != date_value:
                    raise ValueError(f"history[{index}].date должен быть YYYY-MM-DD")
                if status != "completed" and on_time:
                    raise ValueError(f"history[{index}].on_time допустим только для completed")
                history.append(
                    {
                        "employee_id": cls._identifier(item.get("employee_id"), f"history[{index}].employee_id"),
                        "event_id": cls._identifier(item.get("event_id"), f"history[{index}].event_id"),
                        "status": status,
                        "on_time": on_time,
                        "date": date_value,
                    }
                )
            cleaned["history"] = history

        final_employees = {item["employee_id"] for item in current.employees}
        final_employees.update(item["employee_id"] for item in cleaned.get("employees", []))
        final_event_records = cleaned.get("events", current.events)
        final_events = {item["event_id"] for item in final_event_records}
        final_skill_records = cleaned.get("skills", current.skills)
        final_skills = {item["skill_id"] for item in final_skill_records}
        employee_records = {item["employee_id"]: item for item in current.employees}
        employee_records.update({item["employee_id"]: item for item in cleaned.get("employees", [])})
        for employee in employee_records.values():
            for skill_id in employee.get("skills", {}):
                if skill_id not in final_skills:
                    raise ValueError(f"Профиль ссылается на неизвестный skill_id: {skill_id}")
            if not is_terminal_grade(str(employee.get("grade", "Middle"))):
                target = next_grade(str(employee.get("grade", "Middle")))
                has_rubric = any(
                    target in skill.get("requirements", {}).get(employee.get("role"), {})
                    or target in (skill.get("grade_requirements") or skill.get("requirements_by_grade") or {})
                    for skill in final_skill_records
                )
                if not has_rubric:
                    raise ValueError(
                        f"Для роли {employee.get('role')} не настроены требования следующего грейда {target}"
                    )
        for event in final_event_records:
            for gain in event["skills"]:
                if gain["skill_id"] not in final_skills:
                    raise ValueError(f"Активность ссылается на неизвестный skill_id: {gain['skill_id']}")
        if "history" in cleaned:
            uploaded_history_ids = {item["employee_id"] for item in cleaned["history"]}
            final_history = [row for row in current.history if row.get("employee_id") not in uploaded_history_ids] + cleaned["history"]
        else:
            final_history = list(current.history)
        for row in final_history:
            if row["employee_id"] not in final_employees:
                raise ValueError(f"История ссылается на неизвестного сотрудника: {row['employee_id']}")
            if row["event_id"] not in final_events:
                raise ValueError(f"История ссылается на неизвестную активность: {row['event_id']}")
        complexity = len(final_employees) * max(len(final_events), 1) * max(len(final_history), 1)
        if complexity > 25_000_000:
            raise ValueError("Набор слишком сложен для локального демо; уменьшите число профилей, событий или истории")
        return cleaned

    def upload_bundle_validated(self, bundle: dict[str, Any]) -> dict[str, Any]:
        """Validate in a staging engine and commit all-or-nothing."""
        cleaned = self._validate_bundle(bundle, self)
        staged = CareerEngine()
        staged.skills = deepcopy(self.skills)
        staged.events = deepcopy(self.events)
        staged.employees = deepcopy(self.employees)
        staged.history = deepcopy(self.history)
        staged.data_source = self.data_source
        result = staged.upload_bundle(cleaned)
        self.skills = staged.skills
        self.events = staged.events
        self.employees = staged.employees
        self.history = staged.history
        self.data_source = staged.data_source
        return result


def parse_csv_text(text: str) -> list[dict[str, Any]]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        headers = [header.strip().lstrip("\ufeff") for header in next(reader)]
    except StopIteration as exc:
        raise ValueError("CSV-файл пуст") from exc
    if not headers or any(not header for header in headers):
        raise ValueError("CSV содержит пустой заголовок")
    if len(set(headers)) != len(headers):
        raise ValueError("CSV содержит повторяющиеся заголовки")
    rows: list[dict[str, Any]] = []
    for line_number, values in enumerate(reader, start=2):
        if not values or not any(value.strip() for value in values):
            continue
        if len(values) != len(headers):
            raise ValueError(
                f"CSV: строка {line_number} содержит {len(values)} полей вместо {len(headers)}"
            )
        rows.append({header: value.strip() for header, value in zip(headers, values)})
    if not rows:
        raise ValueError("CSV не содержит записей")
    return rows
