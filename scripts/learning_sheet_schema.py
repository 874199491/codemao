"""Helpers for locating learning-sheet columns from generated profile schema."""

from __future__ import annotations

from typing import Any


DEFAULT_ALIASES: dict[str, tuple[str, ...]] = {
    "student_id": ("学生ID", "用户ID", "用户id", "学员ID", "用户编号", "user_id", "userId"),
    "student_name": ("学生姓名", "学员姓名", "学生名字", "孩子姓名", "孩子名字", "姓名"),
    "class_time": ("上课时间", "班级时间", "上课时段", "班次"),
    "class_name": ("班级", "班级名称", "班号"),
    "leave": ("是否请假", "请假"),
    "leave_reason": ("请假原因", "未到课原因", "未完课原因"),
    "phone_followup": ("是否电话跟进", "电话跟进"),
    "focus": ("重点关注",),
}

WEEKLY_FIELD_NAMES = {
    "live": "直播参与情况",
    "solitaire": "接龙",
    "completion": "到课/完课情况",
}


def normalize_header(value: object) -> str:
    return "".join(character for character in str(value or "").strip().lower() if not character.isspace())


def column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def schema(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("learning_sheet_schema")
    return value if isinstance(value, dict) else {}


def schema_column(config: dict[str, Any], field: str) -> dict[str, Any] | None:
    columns = schema(config).get("columns")
    if not isinstance(columns, dict):
        return None
    value = columns.get(field)
    return value if isinstance(value, dict) else None


def schema_weekly_column(config: dict[str, Any], week: int, field: str) -> dict[str, Any] | None:
    weekly = schema(config).get("weekly_columns")
    if not isinstance(weekly, dict):
        return None
    week_value = weekly.get(f"W{week}")
    if not isinstance(week_value, dict):
        return None
    value = week_value.get(field)
    return value if isinstance(value, dict) else None


def index_from_record(headers: list[str], record: dict[str, Any]) -> int | None:
    header = str(record.get("header") or "").strip()
    raw_index = record.get("index")
    try:
        index = int(raw_index) - 1
    except (TypeError, ValueError):
        index = -1
    if 0 <= index < len(headers):
        if not header or normalize_header(headers[index]) == normalize_header(header):
            return index
    if header:
        normalized = [normalize_header(value) for value in headers]
        target = normalize_header(header)
        if target in normalized:
            return normalized.index(target)
    return None


def find_header_index(headers: list[str], *aliases: str) -> int | None:
    normalized_headers = [normalize_header(value) for value in headers]
    normalized_aliases = [normalize_header(value) for value in aliases if str(value or "").strip()]
    for alias in normalized_aliases:
        if alias in normalized_headers:
            return normalized_headers.index(alias)
    for index, header in enumerate(normalized_headers):
        if not header:
            continue
        if any(alias and len(alias) >= 3 and alias in header for alias in normalized_aliases):
            return index
    return None


def required_column(
    headers: list[str],
    config: dict[str, Any],
    field: str,
    *extra_aliases: str,
) -> int:
    record = schema_column(config, field)
    if record:
        index = index_from_record(headers, record)
        if index is not None:
            return index
    aliases = tuple(extra_aliases) + DEFAULT_ALIASES.get(field, ())
    index = find_header_index(headers, *aliases)
    if index is not None:
        return index
    raise RuntimeError(f"Cannot locate required learning-sheet column {field!r}; headers={headers}")


def optional_column(
    headers: list[str],
    config: dict[str, Any],
    field: str,
    *extra_aliases: str,
) -> int | None:
    try:
        return required_column(headers, config, field, *extra_aliases)
    except RuntimeError:
        return None


def required_week_column(
    headers: list[str],
    config: dict[str, Any],
    week: int,
    field: str,
    *extra_aliases: str,
) -> int:
    record = schema_weekly_column(config, week, field)
    if record:
        index = index_from_record(headers, record)
        if index is not None:
            return index
    default_name = f"W{week}{WEEKLY_FIELD_NAMES.get(field, field)}"
    aliases = (default_name, *extra_aliases)
    index = find_header_index(headers, *aliases)
    if index is not None:
        return index
    raise RuntimeError(
        f"Cannot locate required W{week} learning-sheet column {field!r}; headers={headers}"
    )
