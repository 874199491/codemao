#!/usr/bin/env python3
"""Incrementally sync 0724 W1 completion status for one class time."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from build_service_todo import mcp_call
from dingtalk_range_reader import get_complete_range
from learning_sheet_schema import required_column, required_week_column
from teacher_workbench_config import data_prefix, learning_sheet_target, script_config
from week_context import context_for


WORKSPACE = Path(__file__).resolve().parents[1]
CONFIG = script_config()
PREFIX = data_prefix(CONFIG)
TARGET = learning_sheet_target(CONFIG)
NODE_ID = TARGET["node_id"]
SHEET_ID = TARGET["sheet_id"]
CLASS_ID = 0
CLASS_TIME = "周六午"
FINISHED = "已完课"
ATTENDED_NOT_FINISHED = "到课未完课"
ABSENT = "未到课"
FIRST_LESSON_UNFINISHED = "第一课未完成"
OPENED_STATUSES = {FINISHED, ATTENDED_NOT_FINISHED}
READ_RANGE = TARGET["range"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--completion-json",
        type=Path,
        default=WORKSPACE / "data" / f"{PREFIX}-completion-query-latest.json",
    )
    parser.add_argument("--class-id", type=int, default=CLASS_ID)
    parser.add_argument("--class-time", default=CLASS_TIME)
    parser.add_argument("--week", type=int, default=1)
    return parser.parse_args()


def text_cell(value: str) -> dict[str, object]:
    return {"type": "text", "text": value}


def column_letter(index: int) -> str:
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def normalize_header(value: object) -> str:
    text = str(value).strip().lower()
    return "".join(ch for ch in text if not ch.isspace())


def header_index(headers: list[str], *candidates: str) -> int:
    normalized_headers = [normalize_header(value) for value in headers]
    for candidate in candidates:
        normalized = normalize_header(candidate)
        if normalized in normalized_headers:
            return normalized_headers.index(normalized)
    raise ValueError(candidates[0] if candidates else "")


def class_time_matches(actual: str, expected: str) -> bool:
    actual_normalized = actual.replace(" ", "")
    expected_normalized = expected.replace(" ", "")
    if actual_normalized.startswith(expected_normalized):
        return True
    legacy_prefixes = {
        "周五晚": "周五",
        "周六晚": "周六晚",
        "周六午": "周六午",
    }
    fallback = legacy_prefixes.get(expected_normalized)
    return bool(fallback and actual_normalized.startswith(fallback))


def main() -> int:
    args = parse_args()
    payload = json.loads(args.completion_json.read_text(encoding="utf-8"))

    week_context = context_for(week=args.week)
    first_lesson = week_context.first_course
    second_lesson = week_context.second_course
    lesson_statuses: dict[str, dict[int, str]] = {}
    for row in payload.get("detailRows", []):
        if int(row.get("classId") or 0) != args.class_id:
            continue
        lesson_sort = int(row.get("lessonSort") or 0)
        if lesson_sort not in {first_lesson, second_lesson}:
            continue
        lesson_statuses.setdefault(str(row["userId"]), {})[lesson_sort] = str(
            row["status"]
        )

    completion: dict[str, str] = {}
    fallback_completion: dict[str, str] = {}
    crm_class_by_user: dict[str, int] = {}
    all_lesson_statuses: dict[str, dict[int, str]] = {}
    for user_id, lessons in lesson_statuses.items():
        if lessons.get(second_lesson) == FINISHED and lessons.get(first_lesson) == FINISHED:
            status = FINISHED
        elif lessons.get(second_lesson) == FINISHED:
            status = FIRST_LESSON_UNFINISHED
        elif lessons.get(first_lesson) in OPENED_STATUSES:
            status = ATTENDED_NOT_FINISHED
        else:
            status = ABSENT
        completion[user_id] = status
        fallback_completion[user_id] = status

    for row in payload.get("detailRows", []):
        user_id = str(row.get("userId") or "").strip()
        class_id = int(row.get("classId") or 0)
        if user_id and class_id:
            crm_class_by_user[user_id] = class_id
        lesson_sort = int(row.get("lessonSort") or 0)
        if not user_id or lesson_sort not in {first_lesson, second_lesson}:
            continue
        all_lesson_statuses.setdefault(user_id, {})[lesson_sort] = str(row.get("status"))

    for user_id, lessons in all_lesson_statuses.items():
        if user_id in fallback_completion:
            continue
        if lessons.get(second_lesson) == FINISHED and lessons.get(first_lesson) == FINISHED:
            fallback_completion[user_id] = FINISHED
        elif lessons.get(second_lesson) == FINISHED:
            fallback_completion[user_id] = FIRST_LESSON_UNFINISHED
        elif lessons.get(first_lesson) in OPENED_STATUSES:
            fallback_completion[user_id] = ATTENDED_NOT_FINISHED
        else:
            fallback_completion[user_id] = ABSENT

    result = get_complete_range(
        mcp_call,
        node_id=NODE_ID,
        sheet_id=SHEET_ID,
        range_address=READ_RANGE,
    )
    if not result.get("success"):
        raise RuntimeError(f"Cannot read learning sheet: {result}")

    values = result.get("values", [])
    if not values:
        raise RuntimeError("Learning sheet read returned no rows")
    headers = [str(value).strip() for value in values[0]]
    user_id_index = required_column(headers, CONFIG, "student_id")
    name_index = required_column(headers, CONFIG, "student_name")
    class_time_index = required_column(headers, CONFIG, "class_time")
    status_index = required_week_column(
        headers,
        CONFIG,
        args.week,
        "completion",
        f"W{args.week}到课/完课状态",
    )

    status_column = column_letter(status_index + 1)
    changes: list[dict[str, object]] = []
    mismatches: list[dict[str, object]] = []
    expected: Counter[str] = Counter()
    class_rows = 0

    for row_number, row in enumerate(values[1:], start=2):
        padded = list(row) + [""] * (len(headers) - len(row))
        class_time = str(padded[class_time_index]).strip()
        if not class_time_matches(class_time, args.class_time):
            continue
        class_rows += 1
        user_id = str(padded[user_id_index]).strip()
        if user_id in completion:
            new_value = completion[user_id]
        elif user_id in fallback_completion:
            new_value = fallback_completion[user_id]
            mismatches.append(
                {
                    "row": row_number,
                    "userId": user_id,
                    "name": str(padded[name_index]).strip(),
                    "sheetClassTime": class_time,
                    "crmClassId": crm_class_by_user.get(user_id),
                }
            )
        else:
            raise RuntimeError(f"Missing CRM completion for {user_id} at row {row_number}")
        old_value = str(padded[status_index]).strip()
        expected[new_value] += 1
        if old_value != new_value:
            changes.append(
                {
                    "row": row_number,
                    "userId": user_id,
                    "name": str(padded[name_index]).strip(),
                    "old": old_value,
                    "new": new_value,
                }
            )

    for change in changes:
        row_number = int(change["row"])
        write = mcp_call(
            "set_cell_range",
            {
                "nodeId": NODE_ID,
                "sheetId": SHEET_ID,
                "rangeAddress": f"{status_column}{row_number}",
                "cells": [[text_cell(str(change["new"]))]],
            },
        )
        if not write.get("success"):
            raise RuntimeError(f"Cannot update {status_column}{row_number}: {write}")

    verify = get_complete_range(
        mcp_call,
        node_id=NODE_ID,
        sheet_id=SHEET_ID,
        range_address=READ_RANGE,
    )
    actual: Counter[str] = Counter()
    verified_rows = 0
    for row in verify.get("values", [])[1:]:
        padded = list(row) + [""] * (len(headers) - len(row))
        if class_time_matches(str(padded[class_time_index]).strip(), args.class_time):
            verified_rows += 1
            actual[str(padded[status_index]).strip()] += 1

    if verified_rows != class_rows or actual != expected:
        raise RuntimeError(
            f"Verification mismatch: rows={verified_rows}/{class_rows}, "
            f"actual={dict(actual)}, expected={dict(expected)}"
        )

    print(
        json.dumps(
            {
                "classRows": class_rows,
                "classId": args.class_id,
                "classTime": args.class_time,
                "week": args.week,
                "lessons": [first_lesson, second_lesson],
                "crmRows": len(completion),
                "changedCells": len(changes),
                "counts": dict(actual),
                "changes": changes,
                "classIdMismatches": mismatches,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
