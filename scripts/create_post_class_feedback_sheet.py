#!/usr/bin/env python3
"""Create the 0724 post-class accuracy and learning-tier DingTalk sheet."""

from __future__ import annotations

import csv
import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any

from build_service_todo import mcp_call
from learning_sheet_schema import required_column
from teacher_workbench_config import data_prefix, learning_sheet_target, script_config

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


WORKSPACE = Path(__file__).resolve().parents[1]
CONFIG = script_config()
TARGET = learning_sheet_target(CONFIG)
PREFIX = data_prefix(CONFIG)
NODE_ID = TARGET["node_id"]
LEARNING_SHEET_ID = TARGET["sheet_id"]
SHEET_NAME = "课后学情反馈"
OUTPUT_CSV = WORKSPACE / "data" / f"{PREFIX}-post-class-feedback.csv"

HEADERS = [
    "学生ID",
    "学生姓名",
    "上课时间",
    "课中习题正确数",
    "课中习题总数",
    "课中习题正确率",
    "周测正确数",
    "周测题目数",
    "周测正确率",
    "综合正确率",
    "学情分层",
    "数据说明",
    "是否完成笔记",
    "是否已反馈",
    "周次",
    "课程范围",
]


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip().rstrip("%")
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def percent_text(value: float | None) -> str:
    if value is None:
        return ""
    rounded = round(value, 1)
    return f"{int(rounded)}%" if rounded.is_integer() else f"{rounded:.1f}%"


def column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def tier(score: float | None) -> str:
    if score is None:
        return ""
    if score >= 80:
        return "学优"
    if score >= 60:
        return "学中"
    return "学困"


def load_roster() -> list[dict[str, str]]:
    result = mcp_call(
        "get_range",
        {
            "nodeId": NODE_ID,
            "sheetId": LEARNING_SHEET_ID,
            "range": TARGET["range"],
        },
    )
    values = result.get("displayValues") or result.get("values") or []
    if not values:
        raise RuntimeError("The learning-sheet roster is empty")
    headers = [str(value or "").strip() for value in values[0]]
    user_id_index = required_column(headers, CONFIG, "student_id")
    name_index = required_column(headers, CONFIG, "student_name")
    class_time_index = required_column(headers, CONFIG, "class_time")
    roster: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in values[1:]:
        padded = list(row) + [""] * max(0, len(headers) - len(row))
        uid = str(padded[user_id_index] if user_id_index < len(padded) else "").strip()
        if not uid or uid in seen:
            continue
        seen.add(uid)
        roster.append(
            {
                "user_id": uid,
                "name": str(padded[name_index] if name_index < len(padded) else "").strip(),
                "class_time": str(padded[class_time_index] if class_time_index < len(padded) else "").strip(),
            }
        )
    if not roster:
        raise RuntimeError("The learning-sheet roster is empty")
    return roster


def load_course_rows(course_files: list[Path]) -> dict[str, list[dict[str, Any]]]:
    by_user: dict[str, list[dict[str, Any]]] = {}
    for path in course_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        for row in data.get("detailRows") or []:
            uid = str(row.get("user_id") or "")
            if uid:
                by_user.setdefault(uid, []).append(row)
    return by_user


def build_rows(
    roster: list[dict[str, str]],
    by_user: dict[str, list[dict[str, Any]]],
    feedback_statuses: dict[str, bool],
    week: int,
    second_course_number: int,
    mark_week_test_feedback_complete: bool = False,
) -> list[list[str]]:
    output: list[list[str]] = []
    for student in roster:
        uid = student["user_id"]
        course_rows = by_user.get(uid, [])

        regular_right = 0.0
        regular_total = 0.0
        for row in course_rows:
            total = number(row.get("regular_question_count"))
            right = number(row.get("regular_question_finish_count"))
            report_total = number(row.get("study_report_total_question_count"))
            if row.get("is_open") and total and report_total:
                regular_total += total
                regular_right += min(right or 0.0, total)

        question_rate = 100.0 * regular_right / regular_total if regular_total else None

        week_rate: float | None = None
        week_total: float | None = None
        week_right: float | None = None
        course_two = next(
            (
                row
                for row in course_rows
                if int(row.get("course_number") or 0) == second_course_number
            ),
            None,
        )
        note_status = "待确认"
        if course_two and course_two.get("is_open"):
            week_rate = number(course_two.get("week_test_score"))
            report_total = number(course_two.get("study_report_total_question_count"))
            regular_count = number(course_two.get("regular_question_count"))
            if week_rate is not None and report_total is not None and regular_count is not None:
                derived_total = report_total - regular_count
                if derived_total > 0:
                    week_total = derived_total
                    week_right = round(derived_total * week_rate / 100)
            video_work_state = str(course_two.get("video_work_state") or "").upper()
            if video_work_state in {"SUBMIT", "REVIEWED", "UNREVIEW"}:
                note_status = "是"
            elif video_work_state == "NOT_SUBMIT":
                note_status = "否"

        available_rates = [rate for rate in (question_rate, week_rate) if rate is not None]
        combined = sum(available_rates) / len(available_rates) if available_rates else None
        feedback_complete = (
            week_rate is not None
            if mark_week_test_feedback_complete
            else feedback_statuses.get(uid, False)
        )
        first_course_number = second_course_number - 1
        note = (
            f"W{week}第{first_course_number}-{second_course_number}节课中习题；"
            f"第{second_course_number}节周测"
        )
        if not available_rates:
            note = "暂无作答数据，暂不分层"
        elif week_rate is None:
            note += "；周测暂无数据，按课中习题暂评"

        output.append(
            [
                uid,
                student["name"],
                student["class_time"],
                str(int(regular_right)) if regular_total else "",
                str(int(regular_total)) if regular_total else "",
                percent_text(question_rate),
                str(int(week_right)) if week_right is not None else "",
                str(int(week_total)) if week_total is not None else "",
                percent_text(week_rate),
                percent_text(combined),
                tier(combined),
                note,
                note_status,
                "TRUE" if feedback_complete else "FALSE",
                f"W{week}",
                f"第{first_course_number}-{second_course_number}课",
            ]
        )
    return output


def ensure_sheet(sheet_name: str) -> str:
    result = mcp_call("get_all_sheets", {"nodeId": NODE_ID})
    sheets = result.get("sheets") or result.get("value") or result.get("data") or []
    for sheet in sheets:
        if isinstance(sheet, dict) and sheet.get("name") == sheet_name:
            return str(sheet.get("sheetId") or sheet.get("id") or sheet_name)
    created = mcp_call("create_sheet", {"nodeId": NODE_ID, "name": sheet_name})
    sheet_id = created.get("sheetId") or created.get("id")
    return str(sheet_id or sheet_name)


def existing_rows(sheet_id: str) -> list[list[str]]:
    result = mcp_call(
        "get_range",
        {
            "nodeId": NODE_ID,
            "sheetId": sheet_id,
            "range": "A1:P1200",
        },
    )
    values = result.get("displayValues") or result.get("values") or []
    if not values:
        return []
    headers = [str(value).strip() for value in values[0]]
    if not any(headers):
        return []
    if "学生ID" not in headers:
        return []
    rows: list[list[str]] = []
    for row in values[1:]:
        padded = [str(value).strip() for value in row[: len(headers)]]
        padded += [""] * (len(headers) - len(padded))
        if padded[headers.index("学生ID")]:
            rows.append(padded)
    return rows


def existing_feedback_statuses(sheet_id: str, week: int) -> dict[str, bool]:
    result = mcp_call(
        "get_range",
        {
            "nodeId": NODE_ID,
            "sheetId": sheet_id,
            "range": "A1:P1200",
        },
    )
    values = result.get("displayValues") or result.get("values") or []
    if not values:
        return {}
    headers = [str(value).strip() for value in values[0]]
    if "是否已反馈" not in headers or "学生ID" not in headers:
        return {}
    id_index = headers.index("学生ID")
    status_index = headers.index("是否已反馈")
    week_index = headers.index("周次") if "周次" in headers else None
    target_week = f"W{week}"
    statuses: dict[str, bool] = {}
    for row in values[1:]:
        padded = list(row) + [""] * (len(headers) - len(row))
        uid = str(padded[id_index]).strip()
        if not uid:
            continue
        row_week = str(padded[week_index]).strip() if week_index is not None else "W1"
        if row_week.upper() != target_week.upper():
            continue
        statuses[uid] = str(padded[status_index]).strip().lower() in {
            "true",
            "是",
            "1",
            "yes",
        }
    return statuses


def write_csv(rows: list[list[str]], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as file:
        csv.writer(file).writerows([HEADERS, *rows])


def merged_rows(sheet_id: str, rows: list[list[str]], week: int) -> tuple[list[list[str]], int]:
    old_rows = existing_rows(sheet_id)
    target_week = f"W{week}"
    week_index = HEADERS.index("周次")
    normalized: list[list[str]] = []
    replaced = 0
    for row in old_rows:
        padded = list(row) + [""] * (len(HEADERS) - len(row))
        padded = padded[: len(HEADERS)]
        row_week = str(padded[week_index]).strip() or "W1"
        if row_week.upper() == target_week.upper():
            replaced += 1
            continue
        normalized.append(padded)
    combined = [*normalized, *rows]

    def week_number(row: list[str]) -> int:
        label = str(row[week_index]).strip().upper()
        return int(label[1:]) if label.startswith("W") and label[1:].isdigit() else 9999

    combined.sort(
        key=lambda row: (
            week_number(row),
            row[HEADERS.index("上课时间")],
            row[HEADERS.index("学生姓名")],
            row[HEADERS.index("学生ID")],
        )
    )
    return combined, replaced


def write_sheet(sheet_id: str, rows: list[list[str]], week: int) -> tuple[int, int]:
    status_index = HEADERS.index("是否已反馈")
    status_column = column_letter(status_index + 1)
    last_column = column_letter(len(HEADERS))
    combined, replaced = merged_rows(sheet_id, rows, week)
    mcp_call("clear_range", {"nodeId": NODE_ID, "sheetId": sheet_id, "range": f"A:{last_column}"})
    stream = StringIO()
    csv.writer(stream, lineterminator="\n").writerows([HEADERS, *combined])
    result = mcp_call(
        "set_range_from_csv",
        {
            "nodeId": NODE_ID,
            "sheetId": sheet_id,
            "startCell": "A1",
            "csv": stream.getvalue(),
            "allowOverwrite": True,
        },
    )
    if not result.get("success"):
        raise RuntimeError(f"Cannot write {SHEET_NAME}: {result}")

    for start in range(0, len(combined), 80):
        values = combined[start : start + 80]
        first_row = start + 2
        last_row = first_row + len(values) - 1
        checkbox_result = mcp_call(
            "set_cell_range",
            {
                "nodeId": NODE_ID,
                "sheetId": sheet_id,
                "rangeAddress": f"{status_column}{first_row}:{status_column}{last_row}",
                "cells": [
                    [
                        {
                            "dataValidation": {
                                "type": "checkbox",
                                "checked": row[status_index] == "TRUE",
                            }
                        }
                    ]
                    for row in values
                ],
            },
        )
        if not checkbox_result.get("success"):
            raise RuntimeError(
                f"Cannot write feedback checkboxes N{first_row}:N{last_row}: "
                f"{checkbox_result}"
            )
    return len(combined), replaced


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mark-all-feedback-complete",
        action="store_true",
        help="Mark every current roster row as already fed back.",
    )
    parser.add_argument(
        "--mark-week-test-feedback-complete",
        action="store_true",
        help="Mark only current rows with a W1 weekly-test score as already fed back.",
    )
    parser.add_argument("--week", type=int, default=1)
    parser.add_argument("--course-files", type=Path, nargs=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mark_all_feedback_complete and args.mark_week_test_feedback_complete:
        parser.error("Choose only one feedback initialization mode")

    first_course = args.week * 2 - 1
    second_course = first_course + 1
    course_files = args.course_files or [
        WORKSPACE / "data" / f"{PREFIX}-course-{first_course}-feedback.json",
        WORKSPACE / "data" / f"{PREFIX}-course-{second_course}-feedback.json",
    ]
    output_csv = args.output or (
        OUTPUT_CSV
        if args.week == 1
        else WORKSPACE / "data" / f"{PREFIX}-week{args.week}-post-class-feedback.csv"
    )
    sheet_name = SHEET_NAME
    roster = load_roster()
    rows_by_user = load_course_rows(course_files)
    sheet_id = ensure_sheet(sheet_name)
    feedback_statuses = existing_feedback_statuses(sheet_id, args.week)
    if args.mark_all_feedback_complete:
        feedback_statuses = {student["user_id"]: True for student in roster}
    rows = build_rows(
        roster,
        rows_by_user,
        feedback_statuses,
        args.week,
        second_course,
        mark_week_test_feedback_complete=args.mark_week_test_feedback_complete,
    )
    write_csv(rows, output_csv)
    total_sheet_rows, replaced_rows = write_sheet(sheet_id, rows, args.week)

    verify = mcp_call(
        "get_range",
        {
            "nodeId": NODE_ID,
            "sheetId": sheet_id,
            "range": f"A1:{column_letter(len(HEADERS))}8",
        },
    )
    tier_index = HEADERS.index("学情分层")
    note_index = HEADERS.index("是否完成笔记")
    feedback_index = HEADERS.index("是否已反馈")
    tiers = Counter(row[tier_index] or "待评估" for row in rows)
    print(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "sheet_name": sheet_name,
                "sheet_id": sheet_id,
                "week": args.week,
                "week_label": f"W{args.week}",
                "courses": [first_course, second_course],
                "course_range": f"第{first_course}-{second_course}课",
                "roster_rows": len(roster),
                "crm_matched_rows": sum(1 for row in roster if row["user_id"] in rows_by_user),
                "tiers": dict(tiers),
                "notes_submitted": sum(row[note_index] == "是" for row in rows),
                "notes_not_submitted": sum(row[note_index] == "否" for row in rows),
                "notes_pending": sum(row[note_index] == "待确认" for row in rows),
                "feedback_complete": sum(row[feedback_index] == "TRUE" for row in rows),
                "feedback_pending": sum(row[feedback_index] != "TRUE" for row in rows),
                "written_week_rows": len(rows),
                "replaced_week_rows": replaced_rows,
                "total_sheet_rows": total_sheet_rows,
                "csv": str(output_csv),
                "readback": verify.get("displayValues") or verify.get("values") or [],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
