#!/usr/bin/env python3
"""Ensure the latest 0724 week has live and completion columns."""

from __future__ import annotations

import argparse
import json

from build_service_todo import mcp_call
from teacher_workbench_config import learning_sheet_target, script_config


TARGET = learning_sheet_target(script_config())
NODE_ID = TARGET["node_id"]
SHEET_ID = TARGET["sheet_id"]
HEADER_RANGE = TARGET["range"].split(":", 1)[0] + ":" + "".join(
    character for character in TARGET["range"].split(":", 1)[1] if character.isalpha()
) + "3"


def column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", type=int, required=True)
    args = parser.parse_args()
    required = [
        f"W{args.week}直播参与情况",
        f"W{args.week}接龙",
        f"W{args.week}到课/完课情况",
    ]

    result = mcp_call(
        "get_range",
        {"nodeId": NODE_ID, "sheetId": SHEET_ID, "range": HEADER_RANGE},
    )
    values = result.get("displayValues") or result.get("values") or []
    if not values:
        raise RuntimeError("0724 学情表为空")
    headers = [str(value).strip() for value in values[0]]
    occupied = [index for index, value in enumerate(headers, start=1) if value]
    next_index = max(occupied, default=0) + 1
    created: list[dict[str, str]] = []
    located: dict[str, str] = {}

    for header in required:
        if header in headers:
            located[header] = column_letter(headers.index(header) + 1)
            continue
        column = column_letter(next_index)
        write = mcp_call(
            "set_cell_range",
            {
                "nodeId": NODE_ID,
                "sheetId": SHEET_ID,
                "rangeAddress": f"{column}1",
                "cells": [[{"type": "text", "text": header}]],
            },
        )
        if not write.get("success"):
            raise RuntimeError(f"无法创建 {header}: {write}")
        headers.append(header)
        located[header] = column
        created.append({"header": header, "column": column})
        next_index += 1

    print(
        json.dumps(
            {"week": args.week, "columns": located, "created": created},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
