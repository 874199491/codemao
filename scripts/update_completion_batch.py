#!/usr/bin/env python3
"""Update all 0724 class completion statuses with one sheet read and readback."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from build_service_todo import mcp_call
from learning_sheet_schema import required_column, required_week_column
from teacher_workbench_config import class_mappings, learning_sheet_target, script_config
from update_saturday_noon_completion import (
    ABSENT,
    ATTENDED_NOT_FINISHED,
    FINISHED,
    FIRST_LESSON_UNFINISHED,
    NODE_ID,
    OPENED_STATUSES,
    READ_RANGE,
    SHEET_ID,
    class_time_matches,
    column_letter,
    header_index,
    text_cell,
)


WORKSPACE = Path(__file__).resolve().parents[1]
CONFIG = script_config()
TARGET = learning_sheet_target(CONFIG)
NODE_ID = TARGET["node_id"]
SHEET_ID = TARGET["sheet_id"]
READ_RANGE = TARGET["range"]
CLASSES = class_mappings(CONFIG)


def consecutive_batches(changes: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    ordered = sorted(changes, key=lambda item: int(item["row"]))
    batches: list[list[dict[str, object]]] = []
    for change in ordered:
        if not batches:
            batches.append([change])
            continue
        row_number = int(change["row"])
        previous_row = int(batches[-1][-1]["row"])
        if row_number == previous_row + 1:
            batches[-1].append(change)
        else:
            batches.append([change])
    return batches


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--completion-json", type=Path, required=True)
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args()


def status_for(lessons: dict[int, str], first: int, second: int) -> str:
    if lessons.get(second) == FINISHED and lessons.get(first) == FINISHED:
        return FINISHED
    if lessons.get(second) == FINISHED:
        return FIRST_LESSON_UNFINISHED
    if lessons.get(first) in OPENED_STATUSES:
        return ATTENDED_NOT_FINISHED
    return ABSENT


def main() -> int:
    args = parse_args()
    payload = json.loads(args.completion_json.read_text(encoding="utf-8"))
    first_lesson = args.week * 2 - 1
    second_lesson = first_lesson + 1

    lessons_by_class: dict[int, dict[str, dict[int, str]]] = {}
    lessons_all: dict[str, dict[int, str]] = {}
    crm_class_by_user: dict[str, int] = {}
    for item in payload.get("detailRows", []):
        user_id = str(item.get("userId") or "").strip()
        class_id = int(item.get("classId") or 0)
        lesson = int(item.get("lessonSort") or 0)
        if not user_id or not class_id:
            continue
        crm_class_by_user[user_id] = class_id
        if lesson not in {first_lesson, second_lesson}:
            continue
        value = str(item.get("status") or "")
        lessons_by_class.setdefault(class_id, {}).setdefault(user_id, {})[lesson] = value
        lessons_all.setdefault(user_id, {})[lesson] = value

    result = mcp_call(
        "get_range",
        {"nodeId": NODE_ID, "sheetId": SHEET_ID, "range": READ_RANGE},
    )
    if not result.get("success"):
        raise RuntimeError(f"Cannot read learning sheet: {result}")
    values = result.get("values") or result.get("displayValues") or []
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

    summaries: list[dict[str, object]] = []
    all_changes: list[dict[str, object]] = []
    for class_id, class_time in CLASSES:
        class_completion = {
            user_id: status_for(lessons, first_lesson, second_lesson)
            for user_id, lessons in lessons_by_class.get(class_id, {}).items()
        }
        expected: Counter[str] = Counter()
        changes: list[dict[str, object]] = []
        mismatches: list[dict[str, object]] = []
        class_rows = 0
        for row_number, row in enumerate(values[1:], start=2):
            padded = list(row) + [""] * (len(headers) - len(row))
            actual_time = str(padded[class_time_index]).strip()
            if not class_time_matches(actual_time, class_time):
                continue
            class_rows += 1
            user_id = str(padded[user_id_index]).strip()
            if user_id in class_completion:
                new_value = class_completion[user_id]
            elif user_id in lessons_all:
                new_value = status_for(
                    lessons_all[user_id],
                    first_lesson,
                    second_lesson,
                )
                mismatches.append(
                    {
                        "row": row_number,
                        "userId": user_id,
                        "name": str(padded[name_index]).strip(),
                        "sheetClassTime": actual_time,
                        "crmClassId": crm_class_by_user.get(user_id),
                    }
                )
            else:
                raise RuntimeError(
                    f"Missing CRM completion for {user_id} at row {row_number}"
                )
            expected[new_value] += 1
            old_value = str(padded[status_index]).strip()
            if old_value != new_value:
                change = {
                    "row": row_number,
                    "userId": user_id,
                    "name": str(padded[name_index]).strip(),
                    "old": old_value,
                    "new": new_value,
                    "classTime": class_time,
                }
                changes.append(change)
                all_changes.append(change)
        summaries.append(
            {
                "classId": class_id,
                "classTime": class_time,
                "classRows": class_rows,
                "crmRows": len(class_completion),
                "expected": dict(expected),
                "changes": changes,
                "classIdMismatches": mismatches,
            }
        )

    if args.check_only:
        print(
            json.dumps(
                {
                    "checkOnly": True,
                    "week": args.week,
                    "plannedChanges": len(all_changes),
                    "classes": [
                        {
                            **{key: value for key, value in summary.items() if key != "changes"},
                            "plannedChanges": len(summary["changes"]),
                        }
                        for summary in summaries
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    write_batches = consecutive_batches(all_changes)
    for batch in write_batches:
        first_row = int(batch[0]["row"])
        last_row = int(batch[-1]["row"])
        range_address = (
            f"{status_column}{first_row}"
            if first_row == last_row
            else f"{status_column}{first_row}:{status_column}{last_row}"
        )
        write = mcp_call(
            "set_cell_range",
            {
                "nodeId": NODE_ID,
                "sheetId": SHEET_ID,
                "rangeAddress": range_address,
                "cells": [[text_cell(str(change["new"]))] for change in batch],
            },
        )
        if not write.get("success"):
            raise RuntimeError(f"Cannot update {range_address}: {write}")

    if all_changes:
        first_changed_row = min(int(change["row"]) for change in all_changes)
        last_changed_row = max(int(change["row"]) for change in all_changes)
        verify = mcp_call(
            "get_range",
            {
                "nodeId": NODE_ID,
                "sheetId": SHEET_ID,
                "range": (
                    f"{status_column}{first_changed_row}:"
                    f"{status_column}{last_changed_row}"
                ),
            },
        )
        verified_values = verify.get("values") or verify.get("displayValues") or []
        if not verify.get("success") or not verified_values:
            raise RuntimeError(f"Cannot verify changed completion cells: {verify}")
        for change in all_changes:
            offset = int(change["row"]) - first_changed_row
            actual_value = (
                str(verified_values[offset][0]).strip()
                if offset < len(verified_values) and verified_values[offset]
                else ""
            )
            if actual_value != str(change["new"]):
                raise RuntimeError(
                    f"Verification mismatch at {status_column}{change['row']}: "
                    f"actual={actual_value!r}, expected={change['new']!r}"
                )
    for summary in summaries:
        expected = Counter(summary.pop("expected"))
        summary["changedCells"] = len(summary.pop("changes"))
        summary["counts"] = dict(expected)

    print(
        json.dumps(
            {
                "week": args.week,
                "lessons": [first_lesson, second_lesson],
                "sheetReads": 1 + int(bool(all_changes)),
                "changedCells": len(all_changes),
                "writeBatches": len(write_batches),
                "classes": summaries,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
