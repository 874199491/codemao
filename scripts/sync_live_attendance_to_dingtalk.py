#!/usr/bin/env python3
"""Incrementally sync 0724 live attendance to DingTalk sheets through MCP."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from build_service_todo import formula_text, mcp_call


WORKSPACE = Path(__file__).resolve().parents[1]
DATA_DIR = WORKSPACE / "data"
CONFIG_PATH = DATA_DIR / "codemao-class-configs.json"
ATTENDANCE_CSV = DATA_DIR / "0724-opening-ceremony-live-attendance-20260719.csv"
HEADERS = ["开学典礼直播出席", "直播观看时长", "直播匹配微信"]
TARGETS = [
    ("st-48028a86-76042", "Q", "S", False),
    ("教学服务学员表", "I", "K", False),
]


def read_attendance() -> dict[str, list[str]]:
    with ATTENDANCE_CSV.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    return {
        row["学生ID"].strip(): [
            row.get("直播出席状态", ""),
            row.get("观看时长", ""),
            row.get("匹配观看微信", ""),
        ]
        for row in rows
        if row.get("学生ID", "").strip()
    }


def write_range(
    node_id: str,
    sheet_id: str,
    start_column: str,
    end_column: str,
    start_row: int,
    values: list[list[object]],
) -> None:
    end_row = start_row + len(values) - 1
    result = mcp_call(
        "update_range",
        {
            "nodeId": node_id,
            "sheetId": sheet_id,
            "rangeAddress": f"{start_column}{start_row}:{end_column}{end_row}",
            "values": [
                [cell if isinstance(cell, bool) else formula_text(str(cell)) for cell in row]
                for row in values
            ],
        },
    )
    if not bool(result.get("success", result)):
        raise RuntimeError(
            f"MCP write failed for {sheet_id} {start_column}{start_row}:{end_column}{end_row}: "
            f"{json.dumps(result, ensure_ascii=False)}"
        )


def sync_target(
    node_id: str,
    sheet_id: str,
    start_column: str,
    end_column: str,
    attendance: dict[str, list[str]],
    checkbox_first: bool,
) -> dict[str, object]:
    data = mcp_call("get_range", {"nodeId": node_id, "sheetId": sheet_id, "range": f"A:{end_column}"})
    raw_rows = data.get("displayValues") or data.get("values") or data.get("data") or []
    current = [[str(cell).strip() if cell is not None else "" for cell in row] for row in raw_rows]
    if not current:
        raise RuntimeError(f"Sheet {sheet_id} is empty")

    start_index = ord(start_column) - ord("A")
    desired_by_row: dict[int, list[object]] = {1: HEADERS}
    matched_ids: set[str] = set()
    sheet_only: list[str] = []
    for row_number, row in enumerate(current[1:], start=2):
        student_id = row[0] if row else ""
        if not student_id:
            continue
        desired = attendance.get(student_id)
        if desired is None:
            sheet_only.append(student_id)
            continue
        desired_by_row[row_number] = (
            [desired[0] == "已参加直播", desired[1], desired[2]]
            if checkbox_first
            else desired
        )
        matched_ids.add(student_id)

    attendance_only = sorted(set(attendance) - matched_ids)
    if sheet_only or attendance_only:
        raise RuntimeError(
            f"Student ID mismatch for {sheet_id}: sheet_only={sheet_only[:10]}, "
            f"attendance_only={attendance_only[:10]}"
        )

    changed: list[tuple[int, list[object]]] = []
    for row_number, desired in desired_by_row.items():
        row = current[row_number - 1] if row_number <= len(current) else []
        existing = [(row[index] if len(row) > index else "") for index in range(start_index, start_index + 3)]
        desired_visible = [
            "TRUE" if cell is True else "FALSE" if cell is False else str(cell)
            for cell in desired
        ]
        if existing != desired_visible:
            changed.append((row_number, desired))

    batches: list[tuple[int, list[list[object]]]] = []
    for row_number, desired in changed:
        contiguous = batches and row_number == batches[-1][0] + len(batches[-1][1])
        if not contiguous or len(batches[-1][1]) >= 100:
            batches.append((row_number, [desired]))
        else:
            batches[-1][1].append(desired)
    for start_row, values in batches:
        write_range(node_id, sheet_id, start_column, end_column, start_row, values)

    return {
        "sheet": sheet_id,
        "matched_students": len(matched_ids),
        "changed_rows": len(changed),
        "mcp_batches": len(batches),
        "columns": f"{start_column}:{end_column}",
    }


def main() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    node_id = str(config["classes"]["0724"]["dingtalk"]["student_sheet_url"])
    attendance = read_attendance()
    results = [
        sync_target(node_id, sheet_id, start_column, end_column, attendance, checkbox_first)
        for sheet_id, start_column, end_column, checkbox_first in TARGETS
    ]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
