#!/usr/bin/env python3
"""Apply readable formatting to the 0724 DingTalk makeup sheet."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

from build_service_todo import mcp_call
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
SHEET_NAME = TARGET["makeup_sheet_name"]
CSV_PATH = WORKSPACE / "data" / f"{PREFIX}-makeup-sheet.csv"
FORMATTING_UNAVAILABLE = False


def row_style(width: int, value: object) -> list[list[object]]:
    return [[value for _ in range(width)]]


def column_style(values: list[object]) -> list[list[object]]:
    return [[value] for value in values]


def matrix(rows: int, width: int, value: object) -> list[list[object]]:
    return [[value for _ in range(width)] for _ in range(rows)]


def ensure_sheet() -> str:
    result = mcp_call("get_all_sheets", {"nodeId": NODE_ID})
    sheets = result.get("sheets") or result.get("value") or result.get("data") or []
    for sheet in sheets:
        if isinstance(sheet, dict) and sheet.get("name") == SHEET_NAME:
            return str(sheet.get("sheetId") or sheet.get("id") or SHEET_NAME)
    created = mcp_call("create_sheet", {"nodeId": NODE_ID, "name": SHEET_NAME})
    return str(created.get("sheetId") or created.get("id") or SHEET_NAME)


def style_range(sheet_id: str, range_address: str, **styles: object) -> None:
    global FORMATTING_UNAVAILABLE
    if FORMATTING_UNAVAILABLE:
        return
    result = mcp_call(
        "update_range",
        {
            "nodeId": NODE_ID,
            "sheetId": sheet_id,
            "rangeAddress": range_address,
            **styles,
        },
    )
    if not result.get("success", result):
        code = str(result.get("code") or result.get("error") or "")
        if "PREPARE_CALL_TOOL_ERROR" in code or "工具调用准备失败" in str(result):
            FORMATTING_UNAVAILABLE = True
            print(f"warning: DingTalk formatting is temporarily unavailable; skipped remaining formatting ({range_address})")
            return
        raise RuntimeError(f"Cannot format {range_address}: {result}")
    print(f"formatted {range_address}")


def safe_cell(row: list[str], index: int) -> str:
    return str(row[index] if index < len(row) else "").strip()


def truthy_text(value: str) -> bool:
    return value in {"是", "TRUE", "True", "true", "1", "已勾选", "勾选"}


def checkbox_cell(checked: bool) -> dict[str, object]:
    return {"dataValidation": {"type": "checkbox", "checked": checked}}


def restore_checkbox_column(sheet_id: str, column: str, values: list[bool]) -> None:
    if not values:
        return
    result = mcp_call(
        "set_cell_range",
        {
            "nodeId": NODE_ID,
            "sheetId": sheet_id,
            "rangeAddress": f"{column}2:{column}{len(values) + 1}",
            "cells": [[checkbox_cell(value)] for value in values],
        },
    )
    if not result.get("success", result):
        raise RuntimeError(f"Cannot restore checkbox column {column}: {result}")
    print(f"checkbox {column}2:{column}{len(values) + 1}")


def main() -> int:
    if not CSV_PATH.exists():
        raise RuntimeError(f"Makeup-sheet CSV does not exist: {CSV_PATH}")
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    if len(rows) < 2:
        raise RuntimeError("Makeup-sheet CSV has no data")

    sheet_id = ensure_sheet()
    width = min(max(len(rows[0]), 1), 9)
    last_row = len(rows)
    body_count = last_row - 1

    style_range(
        sheet_id,
        "A1:I1",
        backgroundColors=row_style(width, "#1F4E5F"),
        fontColors=row_style(width, "#FFFFFF"),
        fontWeights=row_style(width, "bold"),
        fontSizes=row_style(width, 11),
        horizontalAlignments=row_style(width, "center"),
        verticalAlignments=row_style(width, "middle"),
        rowHeights=row_style(width, 32),
    )

    stripe_colors = [
        ["#F7F9FA" if row_number % 2 == 0 else "#FFFFFF" for _ in range(width)]
        for row_number in range(2, last_row + 1)
    ]
    style_range(
        sheet_id,
        f"A2:I{last_row}",
        backgroundColors=stripe_colors,
        fontColors=matrix(body_count, width, "#263238"),
        fontSizes=matrix(body_count, width, 10),
        horizontalAlignments=matrix(body_count, width, "center"),
        verticalAlignments=matrix(body_count, width, "middle"),
        rowHeights=matrix(body_count, width, 25),
    )

    class_colors = {
        "周五晚": ("#E8F1FA", "#24557A"),
        "周六午": ("#EAF6EE", "#287044"),
        "周六晚": ("#F4ECFA", "#6B3E86"),
    }
    class_bg: list[str] = []
    class_fg: list[str] = []
    status_bg: list[str] = []
    status_fg: list[str] = []
    leave_bg: list[str] = []
    leave_fg: list[str] = []
    phone_bg: list[str] = []
    phone_fg: list[str] = []
    reply_bg: list[str] = []
    reply_fg: list[str] = []
    time_bg: list[str] = []
    time_fg: list[str] = []
    phone_checked: list[bool] = []
    reply_checked: list[bool] = []

    for row in rows[1:]:
        class_bg_color, class_fg_color = class_colors.get(safe_cell(row, 2), ("#EEF2F4", "#52616B"))
        class_bg.append(class_bg_color)
        class_fg.append(class_fg_color)

        if safe_cell(row, 3) == "未到课":
            status_bg.append("#FDECEC")
            status_fg.append("#B42318")
        elif safe_cell(row, 3) == "第一课未完成":
            status_bg.append("#EEEAF8")
            status_fg.append("#5B3F8C")
        else:
            status_bg.append("#FFF1D6")
            status_fg.append("#9A5B00")

        if truthy_text(safe_cell(row, 4)):
            leave_bg.append("#F2EAF8")
            leave_fg.append("#6B3E86")
        else:
            leave_bg.append("#EEF3F5")
            leave_fg.append("#607078")

        is_phone_followed = truthy_text(safe_cell(row, 6))
        phone_checked.append(is_phone_followed)
        if is_phone_followed:
            phone_bg.append("#E8F1FA")
            phone_fg.append("#24557A")
        else:
            phone_bg.append("#EEF3F5")
            phone_fg.append("#607078")

        is_replied = truthy_text(safe_cell(row, 7))
        reply_checked.append(is_replied)
        if is_replied:
            reply_bg.append("#E5F5EA")
            reply_fg.append("#237A3B")
        else:
            reply_bg.append("#EEF3F5")
            reply_fg.append("#607078")

        if safe_cell(row, 8):
            time_bg.append("#E5F5EA")
            time_fg.append("#237A3B")
        else:
            time_bg.append("#F2F4F5")
            time_fg.append("#889399")

    style_range(
        sheet_id,
        f"C2:C{last_row}",
        backgroundColors=column_style(class_bg),
        fontColors=column_style(class_fg),
        fontWeights=column_style(["bold"] * body_count),
    )
    style_range(
        sheet_id,
        f"D2:D{last_row}",
        backgroundColors=column_style(status_bg),
        fontColors=column_style(status_fg),
        fontWeights=column_style(["bold"] * body_count),
    )
    style_range(
        sheet_id,
        f"E2:E{last_row}",
        backgroundColors=column_style(leave_bg),
        fontColors=column_style(leave_fg),
        fontWeights=column_style(["bold"] * body_count),
    )
    style_range(
        sheet_id,
        f"F2:F{last_row}",
        horizontalAlignments=column_style(["left"] * body_count),
        fontColors=column_style(["#4B5563"] * body_count),
    )
    style_range(
        sheet_id,
        f"G2:G{last_row}",
        backgroundColors=column_style(phone_bg),
        fontColors=column_style(phone_fg),
        fontWeights=column_style(["bold"] * body_count),
    )
    style_range(
        sheet_id,
        f"H2:H{last_row}",
        backgroundColors=column_style(reply_bg),
        fontColors=column_style(reply_fg),
        fontWeights=column_style(["bold"] * body_count),
    )
    style_range(
        sheet_id,
        f"I2:I{last_row}",
        backgroundColors=column_style(time_bg),
        fontColors=column_style(time_fg),
        fontWeights=column_style(["bold"] * body_count),
    )
    restore_checkbox_column(sheet_id, "G", phone_checked)
    restore_checkbox_column(sheet_id, "H", reply_checked)

    verify = mcp_call(
        "get_range",
        {
            "nodeId": NODE_ID,
            "sheetId": sheet_id,
            "range": "A1:I8",
        },
    )
    print(
        json.dumps(
            {
                "sheet": SHEET_NAME,
                "sheet_id": sheet_id,
                "formatted_rows": last_row,
                "formatted_columns": width,
                "csv": str(CSV_PATH),
                "readback": verify.get("displayValues") or verify.get("values") or [],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
