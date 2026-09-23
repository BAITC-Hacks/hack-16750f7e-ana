"""Normalize starter-kit input before it can enter the live engine."""

from copy import deepcopy
from datetime import date
import math


def fail(path, message):
    raise ValueError(f"Импорт: {path}: {message}")


def text(value, path):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        fail(path, "ожидается непустая строка длиной до 2000 символов")
    return value.strip()


def number(value, path, low=0, high=5, integer=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(path, "ожидается конечное число")
    if not low <= value <= high or not math.isfinite(value) or (integer and value != int(value)):
        fail(path, f"ожидается {'целое ' if integer else ''}число от {low} до {high}")
    return int(value) if integer else float(value)


def obj(value, path):
    if not isinstance(value, dict):
        fail(path, "ожидается объект")
    return value


def label(value, path):
    if isinstance(value, dict):
        if not value:
            fail(path, "пустой словарь переводов")
        return {text(k, path): text(v, path) for k, v in value.items()}
    return text(value, path)


def records(value, field, id_key, keyed=False, single=False):
    if isinstance(value, dict):
        if single:
            value = [value]
        elif keyed:
            rows = []
            for key, raw in value.items():
                row = dict(obj(raw, f"{field}.{key}"))
                if id_key in row and row[id_key] != key:
                    fail(field, f"ключ {key} не совпадает с {id_key}")
                row[id_key] = key
                rows.append(row)
            value = rows
    if not isinstance(value, list):
        fail(field, "ожидается список или поддерживаемый словарь")
    seen = set()
    result = []
    for i, raw in enumerate(value):
        row = dict(obj(raw, f"{field}[{i}]"))
        identifier = text(row.get(id_key), f"{field}[{i}].{id_key}")
        if identifier in seen:
            fail(field, f"повторный ID {identifier}")
        seen.add(identifier)
        row[id_key] = identifier
        result.append(row)
    return result


def normalize_bundle(bundle):
    obj(bundle, "bundle")
    bundle = deepcopy(bundle)
    warnings = []
    for canonical, alias in [("employees", "profiles"), ("history", "activity_history")]:
        if canonical in bundle and alias in bundle:
            fail(canonical, f"передайте только {canonical} или {alias}")
        if alias in bundle:
            bundle[canonical] = bundle.pop(alias)
    if not any(k in bundle for k in ("employees", "skills", "events", "history")):
        fail("bundle", "нет employees, skills, events или history")
    normalized = {}
    if "employees" in bundle:
        value = bundle["employees"]
        rows = records(value, "employees", "employee_id", keyed=True, single=isinstance(value, dict) and "employee_id" in value)
        for row in rows:
            path = f"employees.{row['employee_id']}"
            row["role"] = text(row.get("role"), path + ".role")
            row["grade"] = text(row.get("grade"), path + ".grade")
            row["name"] = text(row.get("name", row["employee_id"]), path + ".name")
            row["skills"] = {text(k, path + ".skills"): number(v, path + ".skills." + str(k))
                             for k, v in obj(row.get("skills"), path + ".skills").items()}
            if "tenure_months" in row:
                row["tenure_months"] = number(row["tenure_months"], path + ".tenure_months", high=1200)
        normalized["employees"] = [{key: row[key] for key in ("employee_id", "name", "role", "grade", "skills", "tenure_months") if key in row} for row in rows]
    if "skills" in bundle:
        rows = records(bundle["skills"], "skills", "skill_id", keyed=True)
        for row in rows:
            path = f"skills.{row['skill_id']}"
            row["name"] = label(row.get("name"), path + ".name")
            row["type"] = text(row.get("type", "hard"), path + ".type")
            if not any(k in row for k in ("requirements", "grade_requirements", "requirements_by_grade")):
                fail(path, "нужны requirements или grade_requirements/requirements_by_grade")
            requirements = obj(row.get("requirements", {}), path + ".requirements")
            for role, grades in requirements.items():
                text(role, path + ".role")
                for grade, level in obj(grades, path + ".grades").items():
                    text(grade, path + ".grade")
                    number(level, path + ".requirements", low=1)
            for key in ("grade_requirements", "requirements_by_grade"):
                if key in row:
                    for grade, level in obj(row[key], path + "." + key).items():
                        text(grade, path + ".grade")
                        number(level, path + "." + key, low=1)
        normalized["skills"] = rows
    if "events" in bundle:
        rows = records(bundle["events"], "events", "event_id", keyed=True)
        for row in rows:
            path = f"events.{row['event_id']}"
            row["title"] = label(row.get("title", row["event_id"]), path + ".title")
            row["type"] = text(row.get("type", "activity"), path + ".type")
            row["duration_hours"] = number(row.get("duration_hours", 4), path + ".duration_hours", high=10000, integer=False)
            audience = obj(row.get("audience", {}), path + ".audience")
            roles = audience.get("roles", [])
            if not isinstance(roles, list):
                fail(path + ".audience.roles", "ожидается список")
            row["audience"] = {"roles": [text(role, path + ".role") for role in roles]}
            gains = row.get("skills", row.get("skill_gains"))
            if not isinstance(gains, list) or not gains:
                fail(path + ".skills", "нужен непустой список приростов")
            converted, seen = [], set()
            for gain in gains:
                gain = obj(gain, path + ".gain")
                skill = text(gain.get("skill_id", gain.get("skill")), path + ".skill_id")
                if skill in seen:
                    fail(path, f"повторный навык {skill}")
                seen.add(skill)
                converted.append({"skill_id": skill, "gain": number(gain.get("gain", 1), path + ".gain"),
                                  "max_level": number(gain.get("max_level", 5), path + ".max_level", low=1)})
            row["skills"] = converted
            row.pop("skill_gains", None)
        normalized["events"] = rows
    if "history" in bundle:
        if not isinstance(bundle["history"], list):
            fail("history", "ожидается список записей, в том числе преобразованный из CSV")
        rows = []
        for index, raw in enumerate(bundle["history"]):
            path = f"history[{index}]"
            row = dict(obj(raw, path))
            for key in ("employee_id", "event_id", "status"):
                row[key] = text(row.get(key), path + "." + key)
            if row["status"] == "missed":
                row["status"] = "skipped"
            if row["status"] not in {"completed", "skipped", "declined"}:
                fail(path + ".status", "допустимы completed, skipped, declined, missed")
            value = row.get("on_time", False)
            if isinstance(value, str) and value.lower() in {"true", "false", "1", "0", "yes", "no", "да", "нет"}:
                value = value.lower() in {"true", "1", "yes", "да"}
            if not isinstance(value, bool):
                fail(path + ".on_time", "ожидается boolean или CSV true/false")
            row["on_time"] = value
            if row.get("date"):
                try:
                    row["date"] = date.fromisoformat(text(row["date"], path + ".date")).isoformat()
                except ValueError:
                    fail(path + ".date", "ожидается дата YYYY-MM-DD")
            else:
                row["date"] = ""
            rows.append(row)
        normalized["history"] = rows
        if any(not row["date"] for row in rows):
            warnings.append("Есть записи истории без даты: они не учитываются в недельной цели.")
    return normalized, warnings


def validate_references(normalized, employees, skills, events):
    employee_ids = {row["employee_id"] for row in employees}
    skill_ids = {row["skill_id"] for row in skills}
    event_ids = {row["event_id"] for row in events}
    for employee in normalized.get("employees", []):
        for skill in employee["skills"]:
            if skill not in skill_ids:
                fail("employees", f"неизвестный skill_id {skill}")
    for event in normalized.get("events", []):
        for gain in event["skills"]:
            if gain["skill_id"] not in skill_ids:
                fail("events", f"неизвестный skill_id {gain['skill_id']}")
    for row in normalized.get("history", []):
        if row["employee_id"] not in employee_ids or row["event_id"] not in event_ids:
            fail("history", "employee_id или event_id отсутствует в загружаемом/текущем наборе")
