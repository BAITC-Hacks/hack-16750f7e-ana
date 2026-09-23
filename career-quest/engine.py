"""Explainable multi-factor recommendation engine for Career Quest.

The engine deliberately keeps ranking deterministic. An LLM may verbalise the
facts in production, but it never invents the score or changes skill levels.
"""

from __future__ import annotations

import csv
import io
import random
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any


GRADE_ORDER = ["Junior", "Middle", "Senior", "Lead"]


def next_grade(grade: str) -> str:
    try:
        index = GRADE_ORDER.index(grade)
    except ValueError:
        return "Senior"
    return GRADE_ORDER[min(index + 1, len(GRADE_ORDER) - 1)]


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
        if requirements:
            return requirements

        # Compatibility with simpler starter-kit skill schemas.
        for skill in self.skills:
            levels = skill.get("grade_requirements") or skill.get("requirements_by_grade") or {}
            value = levels.get(target)
            if value is not None:
                requirements[skill["skill_id"]] = int(value)
        return requirements

    @staticmethod
    def readiness(levels: dict[str, int], requirements: dict[str, int]) -> float:
        if not requirements:
            return 100.0
        covered = sum(min(float(levels.get(skill_id, 0)) / max(required, 1), 1.0) for skill_id, required in requirements.items())
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

    def recommendations(self, employee_id: str, limit: int = 3) -> list[dict[str, Any]]:
        employee = self.employee(employee_id)
        target = next_grade(str(employee.get("grade", "Middle")))
        requirements = self.requirements(employee, target)
        current_levels = {key: int(value) for key, value in employee.get("skills", {}).items()}
        current_readiness = self.readiness(current_levels, requirements)
        gaps = {skill_id: max(required - current_levels.get(skill_id, 0), 0) for skill_id, required in requirements.items()}
        ranked: list[dict[str, Any]] = []

        for event in self.events:
            # Completed one-off activities stay in history and are not offered
            # again. This makes the route visibly advance after completion.
            already_completed = any(
                row.get("employee_id") == employee_id
                and row.get("event_id") == event.get("event_id")
                and row.get("status") == "completed"
                for row in self.history
            )
            if already_completed:
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
                before = current_levels.get(skill_id, 0)
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
                            "text": f"Добровольный формат, нагрузка около {duration:g} ч.; учитывается при оценке выполнимости.",
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
        employee = deepcopy(self.employee(employee_id))
        target = next_grade(str(employee.get("grade", "Middle")))
        requirements = self.requirements(employee, target)
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
                f"Самый низкий навык — «{lowest['name']}», но он не выбран первым. "
                f"Для перехода на {target} сильнее влияет «{recommendations[0]['affected_skills'][0]['name']}», "
                "а история участия дополнительно меняет приоритет."
            )
        history_rows = []
        for row in reversed([r for r in self.history if r.get("employee_id") == employee_id][-8:]):
            event = self.event(str(row.get("event_id"))) or {}
            history_rows.append({**row, "title": local_text(event.get("title") or row.get("event_id"))})
        return {
            "employee": employee,
            "role_label": ROLE_LABELS.get(str(employee.get("role")), str(employee.get("role"))),
            "target_grade": target,
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
        for item in event.get("skills", []):
            skill_id = item.get("skill_id")
            if not skill_id:
                continue
            before = int(employee.setdefault("skills", {}).get(skill_id, 0))
            employee["skills"][skill_id] = min(before + int(item.get("gain", 1)), int(item.get("max_level", 5)))
        self.history.append(
            {"employee_id": employee_id, "event_id": event_id, "status": "completed", "on_time": True, "date": str(date.today())}
        )
        return self.employee_view(employee_id)

    def hr_view(self) -> dict[str, Any]:
        gaps: Counter[str] = Counter()
        participants = Counter(row.get("status", "unknown") for row in self.history)
        watchlist = []
        without_step = 0
        readiness_values = []
        for employee in self.employees:
            target = next_grade(str(employee.get("grade", "Middle")))
            requirements = self.requirements(employee, target)
            levels = employee.get("skills", {})
            readiness = self.readiness(levels, requirements)
            readiness_values.append(readiness)
            for skill_id, required in requirements.items():
                gaps[skill_id] += max(int(required) - int(levels.get(skill_id, 0)), 0)
            recommendations = self.recommendations(employee["employee_id"])
            if not recommendations:
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
        gap_rows = []
        for skill_id, total_gap in gaps.most_common(7):
            skill = self.skill(skill_id) or {"name": skill_id}
            gap_rows.append({"skill_id": skill_id, "name": local_text(skill.get("name")), "total_gap": total_gap})
        total_history = max(sum(participants.values()), 1)
        return {
            "employees": len(self.employees),
            "average_readiness": round(sum(readiness_values) / max(len(readiness_values), 1), 1),
            "without_step": without_step,
            "completion_rate": round(participants["completed"] / total_history * 100, 1),
            "skill_gaps": gap_rows,
            "participation": [{"status": key, "count": value, "percent": round(value / total_history * 100, 1)} for key, value in participants.items()],
            "watchlist": sorted(watchlist, key=lambda row: row["readiness"])[:8],
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


def parse_csv_text(text: str) -> list[dict[str, Any]]:
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        normalized = dict(row)
        if "on_time" in normalized:
            normalized["on_time"] = str(normalized["on_time"]).lower() in {"1", "true", "yes", "да"}
        rows.append(normalized)
    return rows
