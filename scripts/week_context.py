#!/usr/bin/env python3
"""Shared 0724 cohort week calculation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any


COHORT_START = date(2026, 7, 23)
WEEK_LENGTH_DAYS = 7
WEEK_ACTIVE_DAYS = 5
WORKSPACE = Path(__file__).resolve().parents[1]
CONFIG_PATH = WORKSPACE / "data" / "teacher-workbench-config.json"


@dataclass(frozen=True)
class WeekContext:
    week: int
    start: date
    end: date
    first_course: int
    second_course: int

    @property
    def completion_header(self) -> str:
        return f"W{self.week}到课/完课情况"

    @property
    def live_header(self) -> str:
        return f"W{self.week}直播参与情况"


def context_for(day: date | None = None, week: int | None = None) -> WeekContext:
    week_config = load_week_config()
    cohort_start = week_config["cohort_start"]
    week_length_days = week_config["week_length_days"]
    week_active_days = week_config["week_active_days"]
    if week is None:
        current = day or date.today()
        week = counted_week_for_day(current, cohort_start, week_length_days, week_config["week_breaks"])
    if week < 1:
        raise ValueError("week must be at least 1")
    start = counted_week_start(week, cohort_start, week_length_days, week_config["week_breaks"])
    first_course, second_course = course_numbers_for_week(
        week,
        bool(week_config["has_exam_training_lessons"]),
    )
    return WeekContext(
        week=week,
        start=start,
        end=start + timedelta(days=week_active_days - 1),
        first_course=first_course,
        second_course=second_course,
    )


def load_week_config() -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, json.JSONDecodeError):
            payload = {}
    week_length_days = clamp_int(payload.get("week_length_days"), 1, 14, WEEK_LENGTH_DAYS)
    training_numbers = payload.get("training_course_numbers") or []
    week_breaks = parse_week_breaks(payload.get("week_breaks"))
    return {
        "cohort_start": parse_date(payload.get("cohort_start"), COHORT_START),
        "week_length_days": week_length_days,
        "week_active_days": clamp_int(
            payload.get("week_active_days"),
            1,
            week_length_days,
            WEEK_ACTIVE_DAYS,
        ),
        "has_exam_training_lessons": parse_bool(
            payload.get("has_exam_training_lessons"),
            False,
        ),
        "training_course_numbers": [int(v) for v in training_numbers if str(v).isdigit()],
        "week_breaks": week_breaks,
    }


def parse_week_breaks(value: Any) -> list[tuple[date, date]]:
    """Read configured no-class periods, such as the National Day break."""
    breaks: list[tuple[date, date]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        start = parse_date(item.get("start"), date.min)
        end = parse_date(item.get("end"), date.min)
        if start != date.min and end != date.min and end >= start:
            breaks.append((start, end))
    return breaks


def is_break_week_start(start: date, week_breaks: list[tuple[date, date]]) -> bool:
    return any(break_start <= start <= break_end for break_start, break_end in week_breaks)


def counted_week_for_day(current: date, cohort_start: date, week_length_days: int, week_breaks: list[tuple[date, date]]) -> int:
    """Count only class weeks whose start date is not inside a configured break."""
    if current < cohort_start:
        return 1
    count = 0
    start = cohort_start
    while start <= current:
        if not is_break_week_start(start, week_breaks):
            count += 1
        start += timedelta(days=week_length_days)
    return max(1, count)


def counted_week_start(week: int, cohort_start: date, week_length_days: int, week_breaks: list[tuple[date, date]]) -> date:
    count = 0
    start = cohort_start
    while True:
        if not is_break_week_start(start, week_breaks):
            count += 1
            if count == week:
                return start
        start += timedelta(days=week_length_days)


def _training_course_numbers() -> set[int]:
    """Configured physical course numbers that are training (赛考精讲) lessons.

    Falls back to the Every-10th rule (11, 21, 31, …) when not configured, which
    matches the current 0724 cohort.
    """
    cfg = load_week_config()
    numbers = cfg.get("training_course_numbers") or []
    if numbers:
        return set(numbers)
    return set(range(11, 1000, 10))


def is_exam_training_course(course_number: int) -> bool:
    """Return whether a physical course is a training (赛考精讲) lesson."""
    return int(course_number) in _training_course_numbers()


def regular_course_number(regular_index: int, enabled: bool = False) -> int:
    """Map a counted lesson index to the physical CRM course number.

    Skips every configured training (赛考精讲) course number, so the Nth counted
    lesson may map to a higher physical course number when training lessons are
    inserted between regular lessons.
    """
    if regular_index < 1:
        raise ValueError("regular_index must be at least 1")
    if not enabled:
        return regular_index
    training = _training_course_numbers()
    physical = regular_index
    while physical in training:
        physical += 1
    # walk forward collecting the skip count, then re-map.
    # A training course makes the physical number shift up by one per training
    # lesson inserted before the target counted lesson.
    count = regular_index
    physical = regular_index
    while True:
        # number of training lessons <= physical
        skipped = sum(1 for t in training if t <= physical)
        candidate = regular_index + skipped
        if candidate == physical:
            return physical
        physical = candidate


def regular_course_index(course_number: int, enabled: bool = False) -> int | None:
    """Map a physical CRM course number back to its counted lesson index."""
    if course_number < 1:
        return None
    if not enabled:
        return course_number
    if is_exam_training_course(course_number):
        return None
    training = _training_course_numbers()
    # count how many training lessons appear before this physical course number
    skipped = sum(1 for t in training if t < course_number)
    return course_number - skipped


def course_numbers_for_week(week: int, enabled: bool = False) -> tuple[int, int]:
    if week < 1:
        raise ValueError("week must be at least 1")
    return (
        regular_course_number(week * 2 - 1, enabled),
        regular_course_number(week * 2, enabled),
    )


def parse_date(value: Any, fallback: date) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return fallback


def clamp_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def parse_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on", "是"}:
            return True
        if normalized in {"false", "0", "no", "off", "否"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return fallback
