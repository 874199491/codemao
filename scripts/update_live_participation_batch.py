#!/usr/bin/env python3
"""Update all 0724 live/replay statuses with one sheet read and readback."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from build_service_todo import mcp_call
from dingtalk_range_reader import get_complete_range
from learning_sheet_schema import required_column, required_week_column
from teacher_workbench_config import (
    class_mappings,
    data_path,
    learning_sheet_target,
    script_config,
)
from update_live_participation import (
    ABSENT,
    LIVE,
    NODE_ID,
    READ_RANGE,
    REPLAY,
    SHEET_ID,
    class_time_matches,
    column_letter,
    header_index,
    text_cell,
)


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


def record_value(row: dict[str, str], *aliases: str) -> str:
    for alias in aliases:
        value = str(row.get(alias) or "").strip()
        if value:
            return value
    return ""


def confirmed_refunds_by_class() -> dict[int, list[dict[str, str]]]:
    refunds_path = data_path("confirmed_refunded_json", CONFIG)
    roster_path = data_path("roster_csv", CONFIG)
    if not refunds_path.exists() or not roster_path.exists():
        return {}

    refunds_payload = json.loads(refunds_path.read_text(encoding="utf-8"))
    refund_students = refunds_payload.get("students", [])
    refunds = {
        str(student.get("userId") or "").strip(): {
            "userId": str(student.get("userId") or "").strip(),
            "name": str(student.get("studentName") or "").strip(),
        }
        for student in refund_students
        if isinstance(student, dict) and str(student.get("userId") or "").strip()
    }
    if not refunds:
        return {}

    result: dict[int, list[dict[str, str]]] = {}
    with roster_path.open("r", encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            user_id = record_value(row, "学生ID", "用户ID", "用户id", "学员ID", "用户编号")
            refund = refunds.get(user_id)
            if refund is None:
                continue
            row_class_time = record_value(row, "上课时间", "班级时间", "上课时段", "班次")
            for class_id, class_time in CLASSES:
                if class_time_matches(row_class_time, class_time):
                    result.setdefault(class_id, []).append(refund)
                    break
    return result


def boards_by_class(payload: dict[str, object]) -> dict[int, dict[str, object]]:
    boards = payload.get("boards") or []
    if not boards:
        boards = [
            item.get("board")
            for item in payload.get("rows", [])
            if isinstance(item, dict) and isinstance(item.get("board"), dict)
        ]
    result: dict[int, dict[str, object]] = {}
    for board in boards:
        if not isinstance(board, dict):
            continue
        for raw_class_id in board.get("classIdList") or []:
            class_id = int(raw_class_id or 0)
            if class_id and class_id not in result:
                result[class_id] = board
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--absence-json", type=Path, required=True)
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(args.absence_json.read_text(encoding="utf-8"))
    class_boards = boards_by_class(payload)
    configured_class_ids = {class_id for class_id, _ in CLASSES}
    if not configured_class_ids.intersection(class_boards):
        print(
            json.dumps(
                {
                    "week": args.week,
                    "skipped": True,
                    "reason": "本次 CRM 未返回任何已配置班级的直播看板，已跳过直播参与情况写入。",
                    "absenceJson": str(args.absence_json),
                    "crmBoardCount": payload.get("boardCount", 0),
                    "crmRowCount": payload.get("rowCount", 0),
                    "configuredClassIds": sorted(configured_class_ids),
                    "returnedClassIds": sorted(class_boards),
                    "note": "常见原因：所选周次太早/太旧、CRM 直播看板暂未生成，或当前 CRM 账号没有该周直播看板数据。完课数据可继续更新，直播列保持原样。",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    result = get_complete_range(
        mcp_call,
        node_id=NODE_ID,
        sheet_id=SHEET_ID,
        range_address=READ_RANGE,
    )
    if not result.get("success"):
        raise RuntimeError(f"Cannot read learning sheet: {result}")
    values = result.get("displayValues") or result.get("values") or []
    if not values:
        raise RuntimeError("Learning sheet read returned no rows")
    headers = [str(value).strip() for value in values[0]]
    user_id_index = required_column(headers, CONFIG, "student_id")
    name_index = required_column(headers, CONFIG, "student_name")
    class_time_index = required_column(headers, CONFIG, "class_time")
    live_index = required_week_column(
        headers,
        CONFIG,
        args.week,
        "live",
        f"W{args.week}直播参与情况",
        f"W{args.week}直播状态",
    )
    live_column = column_letter(live_index + 1)
    refunds_by_class = confirmed_refunds_by_class()

    summaries: list[dict[str, object]] = []
    all_changes: list[dict[str, object]] = []
    for class_id, class_time in CLASSES:
        board = class_boards.get(class_id)
        if board is None:
            summaries.append(
                {
                    "classId": class_id,
                    "classTime": class_time,
                    "skipped": True,
                    "reason": "本次 CRM 未返回该班直播看板，可能尚未开课",
                }
            )
            continue
        source_rows = [
            item
            for item in payload.get("rows", [])
            if int(item.get("student", {}).get("classId") or 0) == class_id
        ]
        absent_by_id = {
            str(item["student"]["userId"]): item["student"] for item in source_rows
        }
        raw_board_student_count = int(board.get("studentNum") or 0)
        excluded_refunds = refunds_by_class.get(class_id, [])
        board_student_count = raw_board_student_count - len(excluded_refunds)
        if board_student_count < 0:
            raise RuntimeError(
                f"Invalid effective roster for {class_time}: "
                f"board={raw_board_student_count}, refunds={len(excluded_refunds)}"
            )
        expected: Counter[str] = Counter()
        changes: list[dict[str, object]] = []
        learning_count = 0
        for row_number, row in enumerate(values[1:], start=2):
            padded = list(row) + [""] * (len(headers) - len(row))
            if not class_time_matches(
                str(padded[class_time_index]).strip(),
                class_time,
            ):
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
        roster_mismatch = None
        if learning_count != board_student_count:
            roster_mismatch = {
                "sheet": learning_count,
                "boardRaw": raw_board_student_count,
                "excludedRefunds": len(excluded_refunds),
                "boardEffective": board_student_count,
                "note": "学情表上课时间可能已人工调整，按当前学情表匹配到的学员继续更新",
            }
        summaries.append(
            {
                "classId": class_id,
                "classTime": class_time,
                "sheetStudentCount": learning_count,
                "boardStudentCount": raw_board_student_count,
                "effectiveBoardStudentCount": board_student_count,
                "rosterMismatch": roster_mismatch,
                "excludedRefundedStudents": excluded_refunds,
                "boardLiveWatchCount": board.get("livingWatchUserCount"),
                "boardReplayCount": board.get("visitRecordUserCount"),
                "boardParticipateCount": board.get("participatePersonNum"),
                "expected": dict(expected),
                "changes": changes,
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
                            "plannedChanges": len(summary.get("changes", [])),
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
            f"{live_column}{first_row}"
            if first_row == last_row
            else f"{live_column}{first_row}:{live_column}{last_row}"
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
                    f"{live_column}{first_changed_row}:"
                    f"{live_column}{last_changed_row}"
                ),
            },
        )
        verified_values = verify.get("displayValues") or verify.get("values") or []
        if not verify.get("success") or not verified_values:
            raise RuntimeError(f"Cannot verify changed live cells: {verify}")
        for change in all_changes:
            offset = int(change["row"]) - first_changed_row
            actual_value = (
                str(verified_values[offset][0]).strip()
                if offset < len(verified_values) and verified_values[offset]
                else ""
            )
            if actual_value != str(change["new"]):
                raise RuntimeError(
                    f"Verification mismatch at {live_column}{change['row']}: "
                    f"actual={actual_value!r}, expected={change['new']!r}"
                )
    for summary in summaries:
        if summary.get("skipped"):
            summary["changedCells"] = 0
            continue
        expected = Counter(summary.pop("expected"))
        summary["changedCells"] = len(summary.pop("changes"))
        summary["counts"] = dict(expected)

    print(
        json.dumps(
            {
                "week": args.week,
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
