"""Strict upload parsing adapted from fuse/career-quest/server.py."""
import json
from typing import Any
from engine import parse_csv_text
MAX_UPLOAD_FILES = 8
MAX_UPLOAD_FILE_BYTES = 4_000_000
MAX_UPLOAD_CONTENT_BYTES = 6_000_000
MAX_UPLOAD_FILENAME_LENGTH = 180
UPLOAD_SECTIONS = {"employees", "profiles", "events", "skills", "history", "activity_history"}

def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Повторяющееся поле: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"Недопустимое числовое значение: {value}")


def _guard_json_tree(value: Any, *, max_nodes: int = 100_000, max_depth: int = 32) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise ValueError("JSON содержит слишком много элементов")
        if depth > max_depth:
            raise ValueError("JSON имеет слишком большую глубину")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def _strict_json_file(text: str, filename: str) -> Any:
    try:
        value = json.loads(
            text.lstrip("\ufeff"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        _guard_json_tree(value)
        return value
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValueError(f"{filename}: некорректный JSON ({exc})") from exc


def _canonical_section(section: str) -> str:
    if section in {"employees", "profiles"}:
        return "employees"
    if section in {"history", "activity_history"}:
        return "history"
    return section


def _infer_json_section(value: Any, filename: str) -> tuple[str, Any]:
    lower_name = filename.lower()
    name_hints = {
        "employee": "employees",
        "profile": "employees",
        "event": "events",
        "skill": "skills",
        "history": "history",
    }
    hinted_sections = {section for marker, section in name_hints.items() if marker in lower_name}
    if len(hinted_sections) > 1:
        raise ValueError(f"{filename}: имя файла неоднозначно")
    if hinted_sections:
        section = hinted_sections.pop()
        if isinstance(value, dict):
            if any(key in value for key in ("employee_id", "event_id", "skill_id")):
                value = [value]
        return section, value

    records = value if isinstance(value, list) else [value]
    if not records or not all(isinstance(item, dict) for item in records):
        raise ValueError(f"Не удалось однозначно определить тип файла {filename}")

    is_history = all("employee_id" in item and "event_id" in item and "status" in item for item in records)
    candidates = []
    if is_history:
        candidates.append("history")
    elif all("employee_id" in item for item in records):
        candidates.append("employees")
    if all("event_id" in item and "employee_id" not in item for item in records):
        candidates.append("events")
    if all("skill_id" in item for item in records):
        candidates.append("skills")
    if len(candidates) != 1:
        raise ValueError(f"Не удалось однозначно определить тип файла {filename}")
    section = candidates[0]
    if isinstance(value, dict) and section != "skills":
        value = [value]
    return section, value


def _bundle_from_uploaded_files(files: Any) -> dict[str, Any]:
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_UPLOAD_FILES:
        raise ValueError(f"Можно загрузить от 1 до {MAX_UPLOAD_FILES} файлов")

    bundle: dict[str, Any] = {}
    total_bytes = 0

    def add_section(section: str, value: Any, filename: str) -> None:
        canonical = _canonical_section(section)
        if canonical in bundle:
            raise ValueError(f"{filename}: раздел {canonical} уже загружен другим файлом")
        bundle[canonical] = value

    for index, descriptor in enumerate(files):
        if not isinstance(descriptor, dict) or set(descriptor) != {"name", "content"}:
            raise ValueError(f"files[{index}] должен содержать только name и content")
        filename = descriptor["name"]
        content = descriptor["content"]
        if (
            not isinstance(filename, str)
            or not filename.strip()
            or len(filename) > MAX_UPLOAD_FILENAME_LENGTH
            or "/" in filename
            or "\\" in filename
            or "\x00" in filename
        ):
            raise ValueError(f"files[{index}].name недопустимо")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"{filename}: файл пуст или не является текстовым")
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > MAX_UPLOAD_FILE_BYTES:
            raise ValueError(f"{filename}: файл превышает {MAX_UPLOAD_FILE_BYTES // 1_000_000} МБ")
        total_bytes += content_bytes
        if total_bytes > MAX_UPLOAD_CONTENT_BYTES:
            raise ValueError("Суммарный размер файлов превышает 6 МБ")

        lower_name = filename.lower()
        if lower_name.endswith(".csv"):
            add_section("history", parse_csv_text(content), filename)
            continue
        if not lower_name.endswith(".json"):
            raise ValueError(f"{filename}: поддерживаются только JSON и CSV")

        value = _strict_json_file(content, filename)
        is_record = isinstance(value, dict) and any(
            identifier in value for identifier in ("employee_id", "event_id", "skill_id")
        )
        if isinstance(value, dict) and not is_record and set(value).intersection(UPLOAD_SECTIONS):
            unknown = set(value) - UPLOAD_SECTIONS
            if unknown:
                raise ValueError(f"{filename}: неизвестные разделы: {', '.join(sorted(unknown))}")
            for section, section_value in value.items():
                add_section(section, section_value, filename)
            continue
        section, section_value = _infer_json_section(value, filename)
        add_section(section, section_value, filename)
    return bundle

