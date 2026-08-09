#!/usr/bin/env python3
"""Incrementally sync W1 live/replay participation using header-based columns."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from build_service_todo import mcp_call
from teacher_workbench_config import learning_sheet_target, script_config


WORKSPACE = Path(__file__).resolve().parents[1]
TARGET = learning_sheet_target(script_config())
NODE_ID = TARGET["node_id"]
SHEET_ID = TARGET["sheet_id"]
LIVE = "\u5df2\u53c2\u52a0\u76f4\u64ad"
REPLAY = "\u5df2\u89c2\u770b\u56de\u653e"
ABSENT = "\u672a\u53c2\u52a0\u76f4\u64ad/\u56de\u653e"
READ_RANGE = TARGET["range"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--class-id", type=int, required=True)
    parser.add_argument("--class-time", required=True)
    parser.add_argument("--absence-json", type=Path, required=True)
    parser.add_argument("--week", type=int, default=1)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate the live sheet headers and roster without writing cells.",
    )
    return parser.parse_args()


def text_cell(value: str) -> dict[str, object]:
    return {"type": "text", "text": value}


def column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def normalize_header(value: object) -> str:
    text = str(value).strip().lower()
    return "".join(character for character in text if not character.isspace())


def header_index(headers: list[str], *candidates: str) -> int:
    normalized_headers = [normalize_header(value) for value in headers]
    for candidate in candidates:
        normalized = normalize_header(candidate)
        if normalized in normalized_headers:
            return normalized_headers.index(normalized)
    raise ValueError(candidates[0] if candidates else "")


def class_time_matches(actual: str, expected: str) -> bool:
    actual = actual.replace(" ", "")
    expected = expected.replace(" ", "")
    return actual.startswith(expected) or (
        expected == "周五晚" and actual.startswith("周五")
    )


def main() -> int:
    args = parse_args()
    payload = json.loads(args.absence_json.read_text(encoding="utf-8"))
    class_rows = [
        item
        for item in payload.get("rows", [])
        if int(item.get("student", {}).get("classId") or 0) == args.class_id
    ]
    if not class_rows:
        raise RuntimeError(f"No absence rows found for class {args.class_id}")

    board = class_rows[0]["board"]
    absent_by_id = {
        str(item["student"]["userId"]): item["student"] for item in class_rows
    }
    board_student_count = int(board.get("studentNum") or 0)

    result = mcp_call(
        "get_range",
        {
            "nodeId": NODE_ID,
            "sheetId": SHEET_ID,
            "range": READ_RANGE,
        },
    )
    if not result.get("success"):
        raise RuntimeError(f"Cannot read learning sheet: {result}")

    values = result.get("values") or result.get("displayValues") or []
    if not values:
        raise RuntimeError("Learning sheet read returned no rows")
    headers = [str(value).strip() for value in values[0]]
    try:
        user_id_index = header_index(
            headers,
            "学生ID",
            "用户ID",
            "用户id",
            "学员ID",
        )
        name_index = header_index(headers, "学生姓名", "学员姓名", "学生名字")
        class_time_index = header_index(headers, "上课时间")
        live_index = header_index(
            headers,
            f"W{args.week}直播参与情况",
            f"W{args.week}直播状态",
        )
    except ValueError as error:
        raise RuntimeError(f"Cannot locate required columns from headers: {headers}") from error
    live_column = column_letter(live_index + 1)

    changes: list[dict[str, object]] = []
    expected: Counter[str] = Counter()
    learning_count = 0
    for row_number, row in enumerate(values[1:], start=2):
        padded = list(row) + [""] * (len(headers) - len(row))
        if not class_time_matches(str(padded[class_time_index]).strip(), args.class_time):
            continue
        learning_count += 1
        user_id = str(padded[user_id_index]).strip()
        absent = absent_by_id.get(user_id)
        if absent is None:
            new_value = LIVE
        elif int(absent.get("visitRecordEffectiveTime") or 0) > 0:
            new_value = REPLAY
        else:
            new_value = ABSENT
        expected[new_value] += 1
        old_value = str(padded[live_index]).strip()
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

    if learning_count != board_student_count:
        raise RuntimeError(
            f"Roster mismatch: sheet={learning_count}, board={board_student_count}"
        )

    if args.check_only:
        print(
            json.dumps(
                {
                    "checkOnly": True,
                    "classId": args.class_id,
                    "classTime": args.class_time,
                    "week": args.week,
                    "boardStudentCount": board_student_count,
                    "learningSheetCount": learning_count,
                    "resolvedHeaders": {
                        "studentId": headers[user_id_index],
                        "studentName": headers[name_index],
                        "classTime": headers[class_time_index],
                        "liveParticipation": headers[live_index],
                    },
                    "plannedChanges": len(changes),
                    "expectedCounts": dict(expected),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    for change in changes:
        row_number = int(change["row"])
        write = mcp_call(
            "set_cell_range",
            {
                "nodeId": NODE_ID,
                "sheetId": SHEET_ID,
                "rangeAddress": f"{live_column}{row_number}",
                "cells": [[text_cell(str(change["new"]))]],
            },
        )
        if not write.get("success"):
            raise RuntimeError(f"Cannot update {live_column}{row_number}: {write}")

    verify = mcp_call(
        "get_range",
        {
            "nodeId": NODE_ID,
            "sheetId": SHEET_ID,
            "range": READ_RANGE,
        },
    )
    actual: Counter[str] = Counter()
    verified_rows = 0
    for row in verify.get("values", [])[1:]:
        padded = list(row) + [""] * (len(headers) - len(row))
        if class_time_matches(str(padded[class_time_index]).strip(), args.class_time):
            verified_rows += 1
            actual[str(padded[live_index]).strip()] += 1
    if verified_rows != learning_count or actual != expected:
        raise RuntimeError(
            f"Verification mismatch: rows={verified_rows}/{learning_count}, "
            f"actual={dict(actual)}, expected={dict(expected)}"
        )

    print(
        json.dumps(
            {
                "classId": args.class_id,
                "classTime": args.class_time,
                "week": args.week,
                "boardStudentCount": board_student_count,
                "boardLiveWatchCount": board.get("livingWatchUserCount"),
                "boardReplayCount": board.get("visitRecordUserCount"),
                "boardParticipateCount": board.get("participatePersonNum"),
                "changedCells": len(changes),
                "counts": dict(actual),
                "changes": changes,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
