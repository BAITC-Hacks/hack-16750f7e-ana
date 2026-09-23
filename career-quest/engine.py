"""Explainable multi-factor recommendation engine for Career Quest.

The engine deliberately keeps ranking deterministic. An LLM may verbalise the
facts in production, but it never invents the score or changes skill levels.
"""

from __future__ import annotations

import csv
import io
import math
import random
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
from threading import RLock
from functools import wraps
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
    ("SK_API_DESIGN", "Проектирование API", "hard"),
    ("SK_DATABASES", "Проектирование баз данных", "hard"),
    ("SK_CLOUD", "Облачная архитектура", "hard"),
    ("SK_SECURE_CODING", "Безопасная разработка", "hard"),
    ("SK_TEST_AUTOMATION", "Автоматизация тестирования", "hard"),
    ("SK_CICD", "CI/CD", "hard"),
    ("SK_OBSERVABILITY", "Наблюдаемость систем", "hard"),
    ("SK_PERFORMANCE", "Производительность", "hard"),
    ("SK_DISTRIBUTED_SYSTEMS", "Распределённые системы", "hard"),
    ("SK_CODE_REVIEW", "Код-ревью", "soft"),
    ("SK_INCIDENT_RESPONSE", "Реагирование на инциденты", "hard"),
    ("SK_BANKING_ARCHITECTURE", "Банковская архитектура", "hard"),
    ("SK_STATISTICS", "Статистика", "hard"),
    ("SK_ANALYTICS_PYTHON", "Python для аналитики", "hard"),
    ("SK_EXPERIMENT_DESIGN", "Дизайн экспериментов", "hard"),
    ("SK_DATA_QUALITY", "Качество данных", "hard"),
    ("SK_DATA_MODELING", "Моделирование данных", "hard"),
    ("SK_BI_TOOLS", "BI-инструменты", "hard"),
    ("SK_FORECASTING", "Прогнозирование", "hard"),
    ("SK_RISK_ANALYTICS", "Риск-аналитика", "hard"),
    ("SK_DATA_GOVERNANCE", "Управление данными", "hard"),
    ("SK_ETL", "ETL-процессы", "hard"),
    ("SK_METRIC_DESIGN", "Дизайн метрик", "hard"),
    ("SK_CAUSAL_INFERENCE", "Причинно-следственный анализ", "hard"),
    ("SK_DISCOVERY", "Product discovery", "hard"),
    ("SK_PRIORITIZATION", "Приоритизация", "hard"),
    ("SK_PRODUCT_METRICS", "Продуктовые метрики", "hard"),
    ("SK_ROADMAP", "Управление roadmap", "hard"),
    ("SK_BUSINESS_CASE", "Бизнес-кейс", "hard"),
    ("SK_FINANCIAL_MODELING", "Финансовое моделирование", "hard"),
    ("SK_COMPLIANCE", "Комплаенс", "hard"),
    ("SK_EXPERIMENTATION", "Продуктовые эксперименты", "hard"),
    ("SK_FACILITATION", "Фасилитация", "soft"),
    ("SK_MARKET_ANALYSIS", "Анализ рынка", "hard"),
    ("SK_SERVICE_DESIGN", "Сервис-дизайн", "hard"),
    ("SK_PRODUCT_LEADERSHIP", "Продуктовое лидерство", "soft"),
    ("SK_CUSTOMER_EMPATHY", "Клиентская эмпатия", "soft"),
    ("SK_COMPLAINT_HANDLING", "Работа с жалобами", "soft"),
    ("SK_SERVICE_STANDARDS", "Стандарты сервиса", "hard"),
    ("SK_PRODUCT_KNOWLEDGE", "Знание банковских продуктов", "hard"),
    ("SK_DIGITAL_SUPPORT", "Цифровая поддержка", "hard"),
    ("SK_ETHICAL_SALES", "Этичные продажи", "soft"),
    ("SK_FRAUD_SIGNALS", "Выявление признаков мошенничества", "hard"),
    ("SK_DEESCALATION", "Деэскалация", "soft"),
    ("SK_BILINGUAL_SERVICE", "Двуязычный сервис", "soft"),
    ("SK_QUALITY_CONTROL", "Контроль качества", "hard"),
    ("SK_TEAM_COORDINATION", "Координация команды", "soft"),
    ("SK_COACHING", "Наставничество", "soft"),
]


ROLE_SKILLS = {
    "Backend Engineer": ["SK_PYTHON", "SK_SYSTEM_DESIGN", "SK_PUBLIC_SPEAKING", "SK_COMMUNICATION"],
    "Data Analyst": ["SK_SQL", "SK_DATA_VIS", "SK_DATA_STORY", "SK_COMMUNICATION"],
    "Product Manager": ["SK_PRODUCT_STRATEGY", "SK_CUSTOMER_RESEARCH", "SK_STAKEHOLDER", "SK_PUBLIC_SPEAKING"],
    "Contact Center Specialist": ["SK_COMMUNICATION", "SK_STAKEHOLDER", "SK_RISK", "SK_LEADERSHIP"],
}


# The full catalogue mirrors the 60-skill challenge volume. Four skills per
# role are promotion-critical for Junior/Middle paths; adjacent capabilities
# become explicit requirements on the Lead path instead of diluting the next
# grade score with dozens of unrelated fields.
ROLE_EXTENDED_SKILLS = {
    "Backend Engineer": ["SK_API_DESIGN", "SK_DATABASES", "SK_CLOUD", "SK_SECURE_CODING",
        "SK_TEST_AUTOMATION", "SK_CICD", "SK_OBSERVABILITY", "SK_PERFORMANCE",
        "SK_DISTRIBUTED_SYSTEMS", "SK_CODE_REVIEW", "SK_INCIDENT_RESPONSE", "SK_BANKING_ARCHITECTURE"],
    "Data Analyst": ["SK_STATISTICS", "SK_ANALYTICS_PYTHON", "SK_EXPERIMENT_DESIGN", "SK_DATA_QUALITY",
        "SK_DATA_MODELING", "SK_BI_TOOLS", "SK_FORECASTING", "SK_RISK_ANALYTICS",
        "SK_DATA_GOVERNANCE", "SK_ETL", "SK_METRIC_DESIGN", "SK_CAUSAL_INFERENCE"],
    "Product Manager": ["SK_DISCOVERY", "SK_PRIORITIZATION", "SK_PRODUCT_METRICS", "SK_ROADMAP",
        "SK_BUSINESS_CASE", "SK_FINANCIAL_MODELING", "SK_COMPLIANCE", "SK_EXPERIMENTATION",
        "SK_FACILITATION", "SK_MARKET_ANALYSIS", "SK_SERVICE_DESIGN", "SK_PRODUCT_LEADERSHIP"],
    "Contact Center Specialist": ["SK_CUSTOMER_EMPATHY", "SK_COMPLAINT_HANDLING", "SK_SERVICE_STANDARDS",
        "SK_PRODUCT_KNOWLEDGE", "SK_DIGITAL_SUPPORT", "SK_ETHICAL_SALES", "SK_FRAUD_SIGNALS",
        "SK_DEESCALATION", "SK_BILINGUAL_SERVICE", "SK_QUALITY_CONTROL", "SK_TEAM_COORDINATION", "SK_COACHING"],
}

DEMO_REFERENCE_DATE = date(2026, 9, 23)


def history_is_effective(row: dict[str, Any], as_of: date | None = None) -> bool:
    """Ignore impossible future evidence even if a caller bypassed import checks."""
    value = str(row.get("date", "")).strip()
    if not value:
        return True
    try:
        return date.fromisoformat(value) <= (as_of or date.today())
    except ValueError:
        return False


def history_recency(row: dict[str, Any], as_of: date | None = None) -> float:
    """Old misses fade; an undated legacy record receives neutral half weight."""
    value = str(row.get("date", "")).strip()
    if not value:
        return 0.5
    try:
        age = ((as_of or date.today()) - date.fromisoformat(value)).days
    except ValueError:
        return 0.0
    if age < 0:
        return 0.0
    if age <= 180:
        return 1.0
    if age <= 365:
        return 0.5
    return 0.2


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
                # Role policy: practical Python is already expected at Middle;
                # architecture becomes the sharper differentiator for Senior.
                if role == "Backend Engineer" and skill_id == "SK_PYTHON":
                    requirements[role] = {"Junior": 2, "Middle": 3, "Senior": 3, "Lead": 4}
        for role, extended in ROLE_EXTENDED_SKILLS.items():
            if skill_id in extended:
                requirements[role] = {"Lead": 4}
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
        ("EV_API_REVIEW", "API review: контракт без сюрпризов", "workshop", ["Backend Engineer"], [("SK_PYTHON", 1, 5), ("SK_API_DESIGN", 1, 4)], 4),
        ("EV_DATABASE_LAB", "Лаборатория надёжных данных", "project", ["Backend Engineer"], [("SK_PYTHON", 1, 5), ("SK_DATABASES", 1, 4)], 6),
        ("EV_CLOUD_GAME_DAY", "Cloud GameDay", "workshop", ["Backend Engineer"], [("SK_COMMUNICATION", 1, 5), ("SK_CLOUD", 1, 4)], 5),
        ("EV_SECURE_CODE", "Secure coding challenge", "assessment", ["Backend Engineer"], [("SK_PYTHON", 1, 5), ("SK_SECURE_CODING", 1, 4)], 4),
        ("EV_TEST_AUTOMATION", "Автотесты критичного сервиса", "project", ["Backend Engineer"], [("SK_PYTHON", 1, 5), ("SK_TEST_AUTOMATION", 1, 4)], 7),
        ("EV_OBSERVABILITY_DRILL", "Диагностика production-сигналов", "workshop", ["Backend Engineer"], [("SK_COMMUNICATION", 1, 5), ("SK_OBSERVABILITY", 1, 4)], 3),
        ("EV_INCIDENT_SIM", "Симуляция банковского инцидента", "assessment", ["Backend Engineer"], [("SK_COMMUNICATION", 1, 5), ("SK_INCIDENT_RESPONSE", 1, 4)], 5),
        ("EV_STATS_LAB", "Статистика для продуктового решения", "workshop", ["Data Analyst"], [("SK_SQL", 1, 5), ("SK_STATISTICS", 1, 4)], 5),
        ("EV_ANALYTICS_PYTHON", "Python-пайплайн аналитика", "project", ["Data Analyst"], [("SK_SQL", 1, 5), ("SK_ANALYTICS_PYTHON", 1, 4)], 7),
        ("EV_EXPERIMENT_DESIGN", "Дизайн A/B-эксперимента", "assessment", ["Data Analyst"], [("SK_DATA_STORY", 1, 5), ("SK_EXPERIMENT_DESIGN", 1, 4)], 5),
        ("EV_DATA_QUALITY", "Разбор инцидента качества данных", "workshop", ["Data Analyst"], [("SK_SQL", 1, 5), ("SK_DATA_QUALITY", 1, 4)], 4),
        ("EV_DATA_MODEL", "Модель данных витрины", "project", ["Data Analyst"], [("SK_DATA_VIS", 1, 5), ("SK_DATA_MODELING", 1, 4)], 8),
        ("EV_FORECAST", "Прогноз клиентского спроса", "project", ["Data Analyst"], [("SK_DATA_STORY", 1, 5), ("SK_FORECASTING", 1, 4)], 6),
        ("EV_METRIC_REVIEW", "Защита дерева метрик", "mentoring", ["Data Analyst"], [("SK_DATA_VIS", 1, 5), ("SK_METRIC_DESIGN", 1, 4)], 3),
        ("EV_DISCOVERY_SPRINT", "Discovery: проблема до решения", "project", ["Product Manager"], [("SK_CUSTOMER_RESEARCH", 1, 5), ("SK_DISCOVERY", 1, 4)], 6),
        ("EV_PRIORITY_GAME", "Симуляция продуктовых приоритетов", "workshop", ["Product Manager"], [("SK_PRODUCT_STRATEGY", 1, 5), ("SK_PRIORITIZATION", 1, 4)], 4),
        ("EV_PRODUCT_METRICS", "North Star Metric lab", "workshop", ["Product Manager"], [("SK_PRODUCT_STRATEGY", 1, 5), ("SK_PRODUCT_METRICS", 1, 4)], 4),
        ("EV_ROADMAP_DEFENSE", "Защита roadmap перед комитетом", "assessment", ["Product Manager"], [("SK_PUBLIC_SPEAKING", 1, 5), ("SK_ROADMAP", 1, 4)], 5),
        ("EV_BUSINESS_CASE", "Бизнес-кейс нового сервиса", "project", ["Product Manager"], [("SK_PRODUCT_STRATEGY", 1, 5), ("SK_BUSINESS_CASE", 1, 4)], 7),
        ("EV_COMPLIANCE_CLINIC", "Product × Compliance clinic", "mentoring", ["Product Manager"], [("SK_STAKEHOLDER", 1, 5), ("SK_COMPLIANCE", 1, 4)], 3),
        ("EV_SERVICE_BLUEPRINT", "Service blueprint клиента", "workshop", ["Product Manager"], [("SK_CUSTOMER_RESEARCH", 1, 5), ("SK_SERVICE_DESIGN", 1, 4)], 5),
        ("EV_EMPATHY_LAB", "Лаборатория клиентской эмпатии", "workshop", ["Contact Center Specialist"], [("SK_COMMUNICATION", 1, 5), ("SK_CUSTOMER_EMPATHY", 1, 4)], 4),
        ("EV_COMPLAINT_CASE", "Разбор сложной жалобы", "assessment", ["Contact Center Specialist"], [("SK_STAKEHOLDER", 1, 5), ("SK_COMPLAINT_HANDLING", 1, 4)], 4),
        ("EV_SERVICE_STANDARD", "Стандарты сервиса на практике", "course", ["Contact Center Specialist"], [("SK_COMMUNICATION", 1, 5), ("SK_SERVICE_STANDARDS", 1, 4)], 3),
        ("EV_PRODUCT_CLINIC", "Клиника банковских продуктов", "workshop", ["Contact Center Specialist"], [("SK_RISK", 1, 5), ("SK_PRODUCT_KNOWLEDGE", 1, 4)], 5),
        ("EV_FRAUD_SIM", "Симуляция сигналов мошенничества", "assessment", ["Contact Center Specialist"], [("SK_RISK", 1, 5), ("SK_FRAUD_SIGNALS", 1, 4)], 5),
        ("EV_DEESCALATION", "Деэскалация трудного диалога", "mentoring", ["Contact Center Specialist"], [("SK_COMMUNICATION", 1, 5), ("SK_DEESCALATION", 1, 4)], 3),
        ("EV_QUALITY_CALIBRATION", "Калибровка качества звонков", "workshop", ["Contact Center Specialist"], [("SK_LEADERSHIP", 1, 5), ("SK_QUALITY_CONTROL", 1, 4)], 4),
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
        role_skills = ROLE_SKILLS[role] + ROLE_EXTENDED_SKILLS[role]
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
    today = DEMO_REFERENCE_DATE
    for employee in employees:
        suitable = [e for e in events if not e["audience"]["roles"] or employee["role"] in e["audience"]["roles"]]
        # Eight to twelve meaningful records over the full 24-month window;
        # sparse history is closer to voluntary learning than artificial daily logs.
        records = rng.randint(8, 12)
        for offset in range(records):
            event = suitable[(offset + int(employee["employee_id"][1:])) % len(suitable)]
            status = rng.choices(["completed", "skipped", "declined"], weights=[7, 2, 1])[0]
            days_ago = round(730 * (records - 1 - offset) / max(records - 1, 1))
            history.append(
                {
                    "employee_id": employee["employee_id"],
                    "event_id": event["event_id"],
                    "status": status,
                    "on_time": status == "completed" and rng.random() > 0.18,
                    "date": str(today - timedelta(days=days_ago)),
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


def synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.lock:
            store = getattr(self, "store", None)
            durable = store and not kwargs.get("preview") and method.__name__ in {"reset", "complete", "set_participation", "upload_bundle"}
            if durable:
                from storage import FIELDS
                before = {key: deepcopy(getattr(self, key)) for key in FIELDS}
            try:
                result = method(self, *args, **kwargs)
                if durable:
                    store.save(self)
                return result
            except Exception:
                if durable:
                    for key, value in before.items():
                        setattr(self, key, value)
                raise
    return wrapped


class CareerEngine:
    def __init__(self, storage_path=None) -> None:
        self.lock = RLock()
        self.store = None
        self.reset()
        if storage_path:
            from storage import SnapshotStore
            store = SnapshotStore(storage_path)
            data = store.load()
            if data is not None:
                from data_adapter import normalize_bundle, validate_references
                try:
                    normalized, _ = normalize_bundle({key: data[key] for key in ("employees", "skills", "events", "history")})
                    validate_references(normalized, normalized["employees"], normalized["skills"], normalized["events"])
                    for key, value in data.items():
                        setattr(self, key, set(value) if key == "paused_employees" else value)
                    self.hr_view()
                except Exception as exc:
                    raise RuntimeError("Повреждённое сохранение: восстановите резервную копию; файл не изменён.") from exc
            self.store = store
            if data is None:
                store.save(self)

    @synchronized
    def reset(self) -> None:
        self.revision = getattr(self, "revision", 0) + 1
        self.paused_employees = set()
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
    def readiness(levels: dict[str, int], requirements: dict[str, int]) -> float | None:
        if not requirements:
            return None
        covered = sum(min(float(levels.get(skill_id, 0)) / max(required, 1), 1.0) for skill_id, required in requirements.items())
        return round(covered / len(requirements) * 100, 1)

    def _history_stats(self, employee_id: str, event: dict[str, Any], rows=None) -> dict[str, Any]:
        rows = rows if rows is not None else [
            row for row in self.history
            if row.get("employee_id") == employee_id and history_is_effective(row)
        ]
        candidate_skills = {item.get("skill_id") or item.get("skill") for item in event.get("skills") or event.get("skill_gains") or []}
        related_ids = {
            item["event_id"] for item in self.events
            if item.get("type") == event.get("type") or candidate_skills.intersection(
                gain.get("skill_id") or gain.get("skill") for gain in item.get("skills") or item.get("skill_gains") or []
            )
        }
        related = [row for row in rows if row.get("event_id") in related_ids]
        completed = sum(row.get("status") == "completed" for row in related)
        on_time = sum(row.get("status") == "completed" and bool(row.get("on_time", True)) for row in related)
        missed_rows = [row for row in related if row.get("status") in {"skipped", "missed"}]
        miss_weight = sum(history_recency(row) for row in missed_rows)
        misses = len(missed_rows)
        denominator = max(len(related), 1)
        completion_rate = completed / denominator
        on_time_rate = on_time / max(completed, 1)
        # A voluntary decline is not a negative signal. Skips affect format fit,
        # not capability, are capped, and decay with time.
        fit = 0.45 + 0.35 * completion_rate + 0.20 * on_time_rate - 0.10 * min(miss_weight, 2.5)
        return {
            "completed": completed,
            "on_time": on_time,
            "misses": misses,
            "fit": max(0.05, min(fit, 1.0)),
        }

    @synchronized
    def recommendations(self, employee_id: str, limit: int = 3) -> list[dict[str, Any]]:
        employee = self.employee(employee_id)
        target = next_grade(str(employee.get("grade", "Middle")))
        requirements = self.requirements(employee, target)
        if not requirements:
            return []
        current_levels = {key: int(value) for key, value in employee.get("skills", {}).items()}
        current_readiness = self.readiness(current_levels, requirements)
        gaps = {skill_id: max(required - current_levels.get(skill_id, 0), 0) for skill_id, required in requirements.items()}
        history_rows = [row for row in self.history if row.get("employee_id") == employee_id and history_is_effective(row)]
        completed_ids = {row.get("event_id") for row in history_rows if row.get("status") == "completed"}
        ranked: list[dict[str, Any]] = []

        for event in self.events:
            # Completed one-off activities stay in history and are not offered
            # again. This makes the route visibly advance after completion.
            already_completed = event.get("event_id") in completed_ids
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
                # Required level is the explicit catalogue signal of how
                # critical a capability is for the target grade. It therefore
                # outweighs the tempting but brittle "pick the lowest skill"
                # heuristic represented by relative gap severity.
                relevance_values.append(0.35 * severity + 0.50 * importance + 0.15 * coverage)
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
            history = self._history_stats(employee_id, event, history_rows)
            grade_relevance = min(sum(relevance_values) / max(len(requirements), 1) * 3.2, 1.0)
            impact = min(delta / 14.0, 1.0)
            duration = float(event.get("duration_hours", 4))
            feasibility = max(0.35, min(1.0, 1.12 - duration / 20))
            parts = ScoreParts(grade_relevance, impact, history["fit"], feasibility)
            primary = affected[0]

            if history["completed"]:
                history_fact = f"Ранее завершено похожих активностей: {history['completed']}, вовремя: {history['on_time']}."
            elif history["misses"]:
                history_fact = f"Похожие по формату или навыкам активности пропускались {history['misses']} раз; свежие пропуски умеренно снижают приоритет, старые со временем теряют вес."
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

        ranked.sort(key=lambda item: (-item["score"], -item["readiness_delta"], item["duration_hours"], item["event_id"]))
        return ranked[:limit]

    @synchronized
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
        all_history = [r for r in self.history if r.get("employee_id") == employee_id and history_is_effective(r)]
        all_history.sort(key=lambda row: str(row.get("date", "")))
        history_rows = []
        for row in reversed(all_history[-8:]):
            event = self.event(str(row.get("event_id"))) or {}
            history_rows.append({**row, "title": local_text(event.get("title") or row.get("event_id"))})
        return {
            "employee": employee,
            "role_label": ROLE_LABELS.get(str(employee.get("role")), str(employee.get("role"))),
            "target_grade": target,
            "readiness": self.readiness(levels, requirements),
            "requirements_missing": not bool(requirements),
            "revision": self.revision,
            "decision_source": "deterministic_fallback",
            "ai_used": False,
            "ai_latency_ms": 0,
            "fallback_reason": "not_requested",
            "skills": skill_rows,
            "recommendations": recommendations,
            "history": history_rows,
            "history_total": len(all_history),
            "decision_insight": insight,
            "data_source": self.data_source,
            "gamification": self.gamification(employee_id),
            "participation_paused": employee_id in self.paused_employees,
        }

    @staticmethod
    def time_budget(value: Any) -> float:
        if isinstance(value, bool):
            raise ValueError("Бюджет времени должен быть от 1 до 40 часов")
        try:
            hours = float(value)
        except (TypeError, ValueError):
            raise ValueError("Бюджет времени должен быть от 1 до 40 часов") from None
        if not math.isfinite(hours) or not 1 <= hours <= 40:
            raise ValueError("Бюджет времени должен быть от 1 до 40 часов")
        return hours

    def planner(self, employee_id: str, hours: Any = 8) -> dict[str, Any]:
        hours = self.time_budget(hours)
        ranked = self.recommendations(employee_id, limit=len(self.events))
        options = [item for item in ranked if 0 <= item["duration_hours"] <= hours]
        top = ranked[0] if ranked else None
        if not ranked:
            message = "В каталоге нет подходящих незавершённых активностей. Проверьте пробелы с наставником: отсутствие шага не означает готовность к повышению."
        elif not options:
            message = f"В {hours:g} ч. не помещается ни одна активность каталога. Можно выбрать короткое упражнение наставника или увеличить бюджет — участие добровольно."
        elif top["event_id"] != options[0]["event_id"]:
            message = f"Лучший шаг без ограничения — «{top['title']}» ({top['duration_hours']:g} ч.). В ваш бюджет помещается «{options[0]['title']}»: приоритет пересчитан среди доступных по времени вариантов."
        else:
            message = "Первый вариант имеет наибольший score среди шагов, которые помещаются в ваш бюджет. Сравните время и прогнозируемый прирост перед выбором."
        return {"hours": hours, "options": options, "message": message}

    @staticmethod
    def apply_gains(levels: dict[str, int], event: dict[str, Any]) -> dict[str, int]:
        updated = dict(levels)
        for item in event.get("skills") or event.get("skill_gains") or []:
            skill_id = item.get("skill_id") or item.get("skill")
            if skill_id:
                before = int(levels.get(skill_id, 0))
                updated[skill_id] = max(before, min(before + int(item.get("gain", 1)), int(item.get("max_level", 5))))
        return updated

    def simulate(self, employee_id: str, event_ids: Any, hours: Any = 8) -> dict[str, Any]:
        """Counterfactual projection only: never writes skills, history or XP."""
        budget = self.time_budget(hours)
        if (not isinstance(event_ids, list) or not 1 <= len(event_ids) <= 3
                or any(not isinstance(item, str) for item in event_ids)
                or len(set(event_ids)) != len(event_ids)):
            raise ValueError("Выберите от 1 до 3 разных активностей")
        with self.lock:
            options = {item["event_id"]: item for item in self.planner(employee_id, budget)["options"]}
            if any(event_id not in options for event_id in event_ids):
                raise ValueError("Один из шагов больше недоступен. Обновите варианты.")
            total_hours = sum(options[event_id]["duration_hours"] for event_id in event_ids)
            if total_hours > budget:
                raise ValueError(f"Выбранные шаги требуют {total_hours:g} ч., бюджет — {budget:g} ч.")
            employee = self.employee(employee_id)
            requirements = self.requirements(employee, next_grade(employee.get("grade", "Middle")))
            original = dict(employee.get("skills", {}))
            levels = dict(original)
            before = self.readiness(levels, requirements)
            steps = []
            for event_id in event_ids:
                previous = self.readiness(levels, requirements)
                levels = self.apply_gains(levels, self.event(event_id))
                current = self.readiness(levels, requirements)
                steps.append({"title": options[event_id]["title"], "before": previous,
                              "after": current, "delta": round(current - previous, 1)})
            return {"before": before, "after": self.readiness(levels, requirements),
                    "hours": total_hours, "steps": steps,
                    "closed_gaps": sum(original.get(skill, 0) < required <= levels.get(skill, 0)
                                       for skill, required in requirements.items()),
                    "remaining_gaps": [{"skill_id": skill, "current": levels.get(skill, 0), "required": required,
                                        "gap": required - levels.get(skill, 0)}
                                       for skill, required in requirements.items() if levels.get(skill, 0) < required],
                    "improved_skills_count": sum(value > original.get(skill, 0) for skill, value in levels.items()),
                    "skills": [{"name": local_text((self.skill(skill) or {}).get("name")) or skill,
                                "before": original.get(skill, 0), "after": value}
                               for skill, value in levels.items() if value != original.get(skill, 0)],
                    "note": "Это сценарий по правилам каталога, а не оценка реального обучения. Навыки, история и XP не изменены. Порядок шагов — порядок выбора; эффект пересчитан после каждого шага."}

    @synchronized
    def set_participation(self, employee_id: str, paused: bool) -> dict[str, Any]:
        if not isinstance(paused, bool):
            raise ValueError("paused должен быть boolean")
        with self.lock:
            self.employee(employee_id)
            if paused:
                self.paused_employees.add(employee_id)
            else:
                self.paused_employees.discard(employee_id)
            self.revision += 1
            return {"paused": paused}

    def gamification(self, employee_id: str) -> dict[str, Any]:
        # One-off activities earn XP once, including imported history.
        completed = {}
        for row in self.history:
            if not history_is_effective(row):
                continue
            if row.get("employee_id") == employee_id and row.get("status") == "completed" and row.get("event_id"):
                event_id = row["event_id"]
                if event_id not in completed or str(row.get("date", "")) < str(completed[event_id].get("date", "")):
                    completed[event_id] = row
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        weekly = 0
        for row in completed.values():
            try:
                finished = date.fromisoformat(str(row.get("date", "")))
                weekly += monday <= finished <= today
            except ValueError:
                pass
        count = len(completed)
        xp = count * 100
        badges = [
            {"name": name, "description": description, "earned": count >= threshold}
            for name, description, threshold in [
                ("Первый шаг", "Завершить 1 активность", 1),
                ("Исследователь", "Завершить 3 активности", 3),
                ("Мастер практики", "Завершить 5 активностей", 5),
            ]
        ]
        return {"xp": xp, "level": xp // 300 + 1, "level_xp": xp % 300,
                "level_goal": 300, "completed": count, "weekly_completed": weekly,
                "weekly_goal": 2, "badges": badges}

    @synchronized
    def complete(self, employee_id: str, event_id: str) -> dict[str, Any]:
        with self.lock:
            return self._complete(employee_id, event_id)

    def _complete(self, employee_id: str, event_id: str) -> dict[str, Any]:
        employee = self.employee(employee_id)
        if employee_id in self.paused_employees:
            raise ValueError("Участие на паузе. Возобновите его, когда будете готовы.")
        event = self.event(event_id)
        if not event:
            raise KeyError(f"Активность {event_id} не найдена")
        if any(row.get("employee_id") == employee_id and row.get("event_id") == event_id
               and row.get("status") == "completed" and history_is_effective(row) for row in self.history):
            return {**self.employee_view(employee_id), "reward": {"xp": 0, "already_completed": True}}
        roles = event.get("audience", {}).get("roles", []) if isinstance(event.get("audience"), dict) else []
        if roles and employee.get("role") not in roles:
            raise ValueError("Активность не подходит для этой роли")
        employee["skills"] = self.apply_gains(employee.get("skills", {}), event)
        self.history.append(
            {"employee_id": employee_id, "event_id": event_id, "status": "completed", "on_time": True, "date": str(date.today())}
        )
        self.revision += 1
        return {**self.employee_view(employee_id), "reward": {"xp": 100, "already_completed": False}}

    def no_step_reason(self, employee):
        requirements = self.requirements(employee, next_grade(employee.get("grade", "Middle")))
        if not requirements:
            return "Отсутствуют требования следующего грейда"
        levels = employee.get("skills", {})
        gaps = {key for key, required in requirements.items() if levels.get(key, 0) < required}
        if not gaps:
            return "Нет открытых skill gaps"
        suitable = [event for event in self.events if not event.get("audience", {}).get("roles")
                    or employee.get("role") in event["audience"]["roles"]]
        if not suitable:
            return "Каталог не содержит активности для роли"
        helpful = [event for event in suitable if any(
            (item.get("skill_id") or item.get("skill")) in gaps and item.get("gain", 1) > 0
            and levels.get(item.get("skill_id") or item.get("skill"), 0) < item.get("max_level", 5)
            for item in event.get("skills") or event.get("skill_gains") or [])]
        if not helpful:
            return "Нет активности с доступным приростом для требуемых навыков"
        completed = {row.get("event_id") for row in self.history if row.get("employee_id") == employee["employee_id"]
                     and row.get("status") == "completed" and history_is_effective(row)}
        if all(event["event_id"] in completed for event in helpful):
            return "Все подходящие активности уже завершены"
        return "Нет допустимого следующего шага по текущим ограничениям"

    def activity_participation(self):
        known = {employee["employee_id"] for employee in self.employees}
        grouped = defaultdict(dict)
        for index, row in enumerate(self.history):
            if not history_is_effective(row):
                continue
            person = row.get("employee_id")
            if person not in known:
                continue
            event_id = row.get("event_id")
            status = "skipped" if row.get("status") == "missed" else row.get("status")
            if status not in {"completed", "skipped", "declined"}:
                continue
            key = (status == "completed", str(row.get("date", "")), index)
            previous = grouped[event_id].get(person)
            if previous is None or key > previous[0]:
                grouped[event_id][person] = (key, status)
        result = []
        for event in self.events:
            states = Counter(value[1] for value in grouped[event["event_id"]].values())
            participants = sum(states.values())
            roles = event.get("audience", {}).get("roles", [])
            result.append({"event_id": event["event_id"], "title": local_text(event.get("title")),
                           "eligible_employees": sum(not roles or person.get("role") in roles for person in self.employees),
                           "participants": participants, "completed": states["completed"], "skipped": states["skipped"],
                           "declined": states["declined"],
                           "completion_rate": round(states["completed"] / participants * 100, 1) if participants else 0})
        return result

    @synchronized
    def hr_view(self) -> dict[str, Any]:
        gaps: Counter[str] = Counter()
        gap_people: Counter[str] = Counter()
        current_history = [row for row in self.history if history_is_effective(row)]
        participants = Counter(row.get("status", "unknown") for row in current_history)
        watchlist = []
        without_step = 0
        employees_without_step = []
        requirements_missing = 0
        readiness_values = []
        role_totals: Counter[str] = Counter()
        role_with_step: Counter[str] = Counter()
        for employee in self.employees:
            role = str(employee.get("role", "Unknown"))
            role_totals[role] += 1
            target = next_grade(str(employee.get("grade", "Middle")))
            requirements = self.requirements(employee, target)
            levels = employee.get("skills", {})
            readiness = self.readiness(levels, requirements)
            if readiness is None:
                requirements_missing += 1
            else:
                readiness_values.append(readiness)
            for skill_id, required in requirements.items():
                gap = max(int(required) - int(levels.get(skill_id, 0)), 0)
                gaps[skill_id] += gap
                gap_people[skill_id] += int(gap > 0)
            recommendations = self.recommendations(employee["employee_id"])
            if recommendations:
                role_with_step[role] += 1
            if not recommendations:
                without_step += 1
                employees_without_step.append({"employee_id": employee["employee_id"],
                    "name": employee.get("name", employee["employee_id"]),
                    "role": ROLE_LABELS.get(employee.get("role"), employee.get("role")),
                    "grade": employee.get("grade"), "readiness": readiness, "reason": self.no_step_reason(employee)})
            rows = [row for row in current_history if row.get("employee_id") == employee["employee_id"]]
            participation = sum(row.get("status") == "completed" for row in rows) / max(len(rows), 1) * 100
            if readiness is not None and readiness < 58 and participation < 55 and employee["employee_id"] not in self.paused_employees:
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
        for skill_id, people in gap_people.most_common(7):
            skill = self.skill(skill_id) or {"name": skill_id}
            gap_rows.append({"skill_id": skill_id, "name": local_text(skill.get("name")),
                             "employees_with_gap": people, "share_with_gap": round(people / max(len(self.employees), 1) * 100, 1),
                             "total_gap": gaps[skill_id]})
        total_history = max(sum(participants.values()), 1)
        dated = sorted(date.fromisoformat(str(row["date"])) for row in current_history if row.get("date"))
        history_months = round((dated[-1] - dated[0]).days / (365.25 / 12)) if len(dated) > 1 else 0
        return {
            "employees": len(self.employees),
            "paused_employees": len(self.paused_employees),
            "average_readiness": round(sum(readiness_values) / len(readiness_values), 1) if readiness_values else None,
            "requirements_missing": requirements_missing,
            "employees_without_step": employees_without_step,
            "activity_participation": self.activity_participation(),
            "without_step": without_step,
            "completion_rate": round(participants["completed"] / total_history * 100, 1),
            "skill_gaps": gap_rows,
            "participation": [{"status": key, "count": value, "percent": round(value / total_history * 100, 1)} for key, value in participants.items()],
            "watchlist": sorted(watchlist, key=lambda row: row["readiness"])[:8],
            "dataset_stats": {"employees": len(self.employees), "events": len(self.events), "skills": len(self.skills),
                              "history_records": len(current_history), "history_months": history_months},
            "coverage_by_role": [{"role": ROLE_LABELS.get(role, role), "employees": count,
                                  "with_step": role_with_step[role],
                                  "coverage": round(role_with_step[role] / count * 100, 1)}
                                 for role, count in sorted(role_totals.items())],
            "data_source": self.data_source,
        }

    @synchronized
    def upload_bundle(self, bundle: dict[str, Any], *, mode="legacy", preview=False) -> dict[str, Any]:
        from data_adapter import normalize_bundle, validate_references
        normalized, warnings = normalize_bundle(bundle)
        if mode not in {"legacy", "merge", "replace"}:
            raise ValueError("Импорт: mode: допустимы merge или replace")
        if mode == "replace" and set(normalized) != {"employees", "events", "skills", "history"}:
            raise ValueError("Импорт: для замены нужны employees, events, skills и history")
        temporary = object.__new__(CareerEngine)
        temporary.lock = RLock()
        for key in ("employees", "events", "skills", "history", "paused_employees", "revision", "data_source"):
            setattr(temporary, key, deepcopy(getattr(self, key)))
        if mode == "replace":
            temporary.employees, temporary.events, temporary.skills, temporary.history = [], [], [], []
            temporary.paused_employees = set()
        employees = normalized.get("employees", [])
        known = {row["employee_id"] for row in employees}
        temporary.employees = [row for row in temporary.employees if row["employee_id"] not in known] + employees
        for key in ("events", "skills"):
            if key in normalized:
                if mode == "merge":
                    id_key = "event_id" if key == "events" else "skill_id"
                    updated = {row[id_key]: row for row in getattr(temporary, key)}
                    updated.update({row[id_key]: row for row in normalized[key]})
                    setattr(temporary, key, list(updated.values()))
                else:
                    setattr(temporary, key, normalized[key])
        if "history" in normalized:
            rows = normalized["history"]
            replaced = {row["employee_id"] for row in rows} or known
            if mode == "merge":
                temporary.history += [row for row in rows if row not in temporary.history]
            else:
                temporary.history = [row for row in temporary.history if row.get("employee_id") not in replaced] + rows
                warnings.append("История переданных сотрудников заменяется; остальные записи сохранены.")
            if not rows and not replaced:
                warnings.append("Пустая история без employees: существующая история сохранена.")
        validate_references(normalized, temporary.employees, temporary.skills, temporary.events)
        try:
            missing = sum(temporary.employee_view(row["employee_id"])["requirements_missing"] for row in temporary.employees)
            temporary.hr_view()
        except (TypeError, ValueError, KeyError, AttributeError, OverflowError) as exc:
            raise ValueError("Импорт: набор несовместим с расчётом профиля и рекомендаций") from exc
        if missing:
            warnings.append(f"У {missing} сотрудников нет требований следующего грейда; готовность недоступна.")
        event_ids = {row["event_id"] for row in temporary.events}
        if any(row.get("event_id") not in event_ids for row in temporary.history):
            warnings.append("В сохранённой истории есть активности вне нового каталога; они не входят в таблицу участия по активностям.")
        temporary.data_source = "Загруженный набор жюри"
        if mode == "merge" and self.data_source == "Встроенный демо-набор":
            temporary.data_source = "Демо-набор + импортированные данные"
            warnings.append("Импорт дополняет синтетический демо-набор. Для отдельного набора выберите замену.")
        if preview:
            return {"counts": {key: len(normalized.get(key, [])) for key in ("employees", "events", "skills", "history")},
                    "warnings": warnings, "mode": mode, "revision": self.revision}
        for key in ("employees", "events", "skills", "history", "data_source", "paused_employees"):
            setattr(self, key, getattr(temporary, key))
        self.revision += 1
        counts = {key: len(getattr(self, key)) for key in ("employees", "events", "skills", "history")}
        return {**counts, "counts": counts, "warnings": warnings, "data_source": self.data_source}


def parse_csv_text(text: str) -> list[dict[str, Any]]:
    text = text.lstrip("\ufeff")
    delimiter = ";" if ";" in text.splitlines()[0] else ","
    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter, strict=True)
        headers = reader.fieldnames or []
        if len(set(headers)) != len(headers) or not {"employee_id", "event_id", "status"} <= set(headers):
            raise ValueError("CSV: нужны уникальные заголовки employee_id,event_id,status")
        rows = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError("CSV: число полей не совпадает с заголовком")
        return rows
    except csv.Error as exc:
        raise ValueError("CSV: повреждённая строка или кавычки") from exc
