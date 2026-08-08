#!/usr/bin/env python3
"""Create and sync the 0724 makeup roster to DingTalk through MCP."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from io import StringIO
from pathlib import Path
from typing import Any

from build_service_todo import mcp_call
from learning_sheet_schema import optional_column, required_column, required_week_column
from teacher_workbench_config import data_prefix, learning_sheet_target, script_config

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


WORKSPACE = Path(__file__).resolve().parents[1]
CONFIG = script_config()
PREFIX = data_prefix(CONFIG)
TARGET = learning_sheet_target(CONFIG)
NODE_ID = TARGET["node_id"]
LEARNING_SHEET_ID = TARGET["sheet_id"]
SHEET_NAME = TARGET["makeup_sheet_name"]
REPLY_JSON = WORKSPACE / "data" / f"{PREFIX}-makeup-reminder-replies-20260726.json"
OUTPUT_CSV = WORKSPACE / "data" / f"{PREFIX}-makeup-sheet.csv"
MANUAL_TIME_ARCHIVE = WORKSPACE / "data" / f"{PREFIX}-makeup-time-archive.json"
MANUAL_PHONE_ARCHIVE = WORKSPACE / "data" / f"{PREFIX}-makeup-phone-followup-archive.json"
TARGET_STATUSES = {"未到课", "未完课", "到课未完课", "第一课未完成"}
HEADERS = [
    "学生ID",
    "学生名字",
    "上课时间",
    "完课情况",
    "是否请假",
    "请假原因",
    "是否电话跟进",
    "补课时间",
]
LEARNING_RANGE = TARGET["range"]


def normalize_header(value: object) -> str:
    text = str(value).strip().lower()
    return "".join(ch for ch in text if not ch.isspace())


def header_index(
    headers: list[str],
    name: str,
    *aliases: str,
    required: bool = True,
) -> int | None:
    normalized_headers = [normalize_header(value) for value in headers]
    for candidate in (name, *aliases):
        normalized = normalize_header(candidate)
        if normalized in normalized_headers:
            return normalized_headers.index(normalized)
    if required:
        all_names = (name, *aliases)
        raise RuntimeError(f"Cannot locate required column {all_names!r} from headers: {headers}")
    return None


def read_learning_rows() -> list[list[Any]]:
    result = mcp_call(
        "get_range",
        {"nodeId": NODE_ID, "sheetId": LEARNING_SHEET_ID, "range": LEARNING_RANGE},
    )
    values = result.get("displayValues") or result.get("values") or []
    if not values:
        raise RuntimeError("The learning sheet is empty")
    return values


def reply_times() -> dict[str, str]:
    if not REPLY_JSON.exists():
        return {}
    data = json.loads(REPLY_JSON.read_text(encoding="utf-8"))
    result: dict[str, str] = {}
    for row in data.get("rows") or []:
        uid = str(row.get("user_id") or "")
        value = str(row.get("makeup_time") or "").strip()
        if not uid or not value:
            continue
        if value in {"已回复，时间未明确", "家长语音回复，时间待确认"}:
            continue
        result[uid] = value
    return result


def preserved_makeup_times(sheet_id: str) -> dict[str, str]:
    archive: dict[str, str] = {}
    if MANUAL_TIME_ARCHIVE.exists():
        raw = json.loads(MANUAL_TIME_ARCHIVE.read_text(encoding="utf-8"))
        archive = {str(key): str(value) for key, value in raw.items() if str(value).strip()}

    result = mcp_call(
        "get_range",
        {"nodeId": NODE_ID, "sheetId": sheet_id, "range": "A1:H300"},
    )
    values = result.get("displayValues") or result.get("values") or []
    if values:
        headers = [str(value).strip() for value in values[0]]
        id_index = header_index(headers, "学生ID", "用户ID", "用户id", "学员ID")
        time_index = header_index(headers, "补课时间")
        for row in values[1:]:
            padded = list(row) + [""] * (len(headers) - len(row))
            uid = str(padded[id_index]).strip()
            if not uid:
                continue
            value = str(padded[time_index]).strip()
            if value:
                archive[uid] = value
            else:
                archive.pop(uid, None)

    MANUAL_TIME_ARCHIVE.write_text(
        json.dumps(archive, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return archive


def preserved_phone_followups(sheet_id: str) -> dict[str, str]:
    archive: dict[str, str] = {}
    if MANUAL_PHONE_ARCHIVE.exists():
        raw = json.loads(MANUAL_PHONE_ARCHIVE.read_text(encoding="utf-8"))
        archive = {
            str(key): str(value)
            for key, value in raw.items()
            if str(value).strip() in {"是", "否"}
        }

    result = mcp_call(
        "get_range",
        {"nodeId": NODE_ID, "sheetId": sheet_id, "range": "A1:H300"},
    )
    values = result.get("displayValues") or result.get("values") or []
    if values:
        headers = [str(value).strip() for value in values[0]]
        id_index = header_index(headers, "学生ID", "用户ID", "用户id", "学员ID")
        phone_index = header_index(headers, "是否电话跟进")
        for row in values[1:]:
            padded = list(row) + [""] * (len(headers) - len(row))
            uid = str(padded[id_index]).strip()
            if not uid:
                continue
            value = str(padded[phone_index]).strip()
            archive[uid] = value if value in {"是", "否"} else "否"

    MANUAL_PHONE_ARCHIVE.write_text(
        json.dumps(archive, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return archive


def is_leave(value: str, reason: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"true", "是", "1", "yes"}:
        return "是"
    if reason.strip():
        return "是"
    return "否"


def build_rows(
    values: list[list[Any]],
    makeup_times: dict[str, str],
    phone_followups: dict[str, str],
    week: int,
) -> list[list[str]]:
    headers = [str(value).strip() for value in values[0]]
    user_id_index = required_column(headers, CONFIG, "student_id")
    name_index = required_column(headers, CONFIG, "student_name")
    class_time_index = required_column(headers, CONFIG, "class_time")
    status_index = required_week_column(
        headers,
        CONFIG,
        week,
        "completion",
        f"W{week}到课/完课状态",
    )
    leave_index = optional_column(headers, CONFIG, "leave")
    reason_index = optional_column(
        headers,
        CONFIG,
        "leave_reason",
        "没有来参加直播/未完课原因",
        "没看直播/未到原因",
    )

    output: list[list[str]] = []
    for row in values[1:]:
        padded = list(row) + [""] * (len(headers) - len(row))
        uid = str(padded[user_id_index]).strip()
        status = str(padded[status_index]).strip()
        if not uid or status not in TARGET_STATUSES:
            continue
        leave_value = str(padded[leave_index]).strip() if leave_index is not None else ""
        leave_reason = str(padded[reason_index]).strip() if reason_index is not None else ""
        output.append(
            [
                uid,
                str(padded[name_index]).strip(),
                str(padded[class_time_index]).strip(),
                status,
                is_leave(leave_value, leave_reason),
                leave_reason,
                phone_followups.get(uid, "否"),
                makeup_times.get(uid, ""),
            ]
        )
    return output


def ensure_sheet() -> str:
    result = mcp_call("get_all_sheets", {"nodeId": NODE_ID})
    sheets = result.get("sheets") or result.get("value") or result.get("data") or []
    for sheet in sheets:
        if isinstance(sheet, dict) and sheet.get("name") == SHEET_NAME:
            return str(sheet.get("sheetId") or sheet.get("id") or SHEET_NAME)
    created = mcp_call("create_sheet", {"nodeId": NODE_ID, "name": SHEET_NAME})
    return str(created.get("sheetId") or created.get("id") or SHEET_NAME)


def write_local_csv(rows: list[list[str]]) -> None:
    with OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as file:
        csv.writer(file).writerows([HEADERS, *rows])


def write_sheet(sheet_id: str, rows: list[list[str]]) -> None:
    mcp_call("clear_range", {"nodeId": NODE_ID, "sheetId": sheet_id, "range": "A:H"})
    stream = StringIO()
    csv.writer(stream, lineterminator="\n").writerows([HEADERS, *rows])
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", type=int, default=1)
    args = parser.parse_args()
    sheet_id = ensure_sheet()
    makeup_times = reply_times()
    makeup_times.update(preserved_makeup_times(sheet_id))
    phone_followups = preserved_phone_followups(sheet_id)
    rows = build_rows(read_learning_rows(), makeup_times, phone_followups, args.week)
    write_local_csv(rows)
    write_sheet(sheet_id, rows)
    verify = mcp_call(
        "get_range",
        {
            "nodeId": NODE_ID,
            "sheetId": sheet_id,
            "range": f"A1:H{min(len(rows) + 1, 10)}",
        },
    )
    print(
        json.dumps(
            {
                "sheet_name": SHEET_NAME,
                "sheet_id": sheet_id,
                "week": args.week,
                "rows": len(rows),
                "status_counts": dict(Counter(row[3] for row in rows)),
                "leave_count": sum(row[4] == "是" for row in rows),
                "reason_count": sum(bool(row[5]) for row in rows),
                "phone_followup_count": sum(row[6] == "是" for row in rows),
                "makeup_time_count": sum(bool(row[7]) for row in rows),
                "archived_makeup_time_count": len(
                    json.loads(MANUAL_TIME_ARCHIVE.read_text(encoding="utf-8"))
                ),
                "archived_phone_followup_count": len(phone_followups),
                "readback": verify.get("displayValues") or verify.get("values") or [],
                "csv": str(OUTPUT_CSV),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
