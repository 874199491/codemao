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
        elapsed = (current - cohort_start).days
        week = max(1, elapsed // week_length_days + 1)
    if week < 1:
        raise ValueError("week must be at least 1")
    start = cohort_start + timedelta(days=(week - 1) * week_length_days)
    first_course = week * 2 - 1
    return WeekContext(
        week=week,
        start=start,
        end=start + timedelta(days=week_active_days - 1),
        first_course=first_course,
        second_course=first_course + 1,
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
    return {
        "cohort_start": parse_date(payload.get("cohort_start"), COHORT_START),
        "week_length_days": week_length_days,
        "week_active_days": clamp_int(
            payload.get("week_active_days"),
            1,
            week_length_days,
            WEEK_ACTIVE_DAYS,
        ),
    }


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
