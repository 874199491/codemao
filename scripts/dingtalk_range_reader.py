#!/usr/bin/env python3
"""Read large DingTalk ranges without losing rows to the MCP cell limit."""

from __future__ import annotations

import re
from typing import Callable


RangeCall = Callable[[str, dict[str, object]], dict[str, object]]


def _column_number(column: str) -> int:
    value = 0
    for character in column:
        value = value * 26 + ord(character) - 64
    return value


def _rows(result: dict[str, object], key: str) -> list[list[object]]:
    value = result.get(key)
    return value if isinstance(value, list) else []


def _pad(rows: list[list[object]], count: int) -> list[list[object]]:
    normalized = [row if isinstance(row, list) else [] for row in rows[:count]]
    normalized.extend([[] for _ in range(count - len(normalized))])
    return normalized


def get_complete_range(
    mcp_call: RangeCall,
    *,
    node_id: str,
    sheet_id: str,
    range_address: str,
    max_cells_per_call: int = 10_000,
) -> dict[str, object]:
    """Return a normal get_range result, chunking explicit row ranges safely."""
    address = str(range_address or "").strip().upper()
    match = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", address)
    if not match:
        return mcp_call(
            "get_range",
            {"nodeId": node_id, "sheetId": sheet_id, "range": range_address},
        )

    start_col, start_text, end_col, end_text = match.groups()
    start_row, end_row = int(start_text), int(end_text)
    width = _column_number(end_col) - _column_number(start_col) + 1
    if width <= 0 or end_row < start_row:
        raise ValueError(f"Invalid DingTalk range: {range_address}")
    rows_per_call = max(1, max_cells_per_call // width)

    combined_values: list[list[object]] = []
    combined_display: list[list[object]] = []
    has_values = False
    has_display = False
    last_result: dict[str, object] = {"success": True}
    for chunk_start in range(start_row, end_row + 1, rows_per_call):
        chunk_end = min(end_row, chunk_start + rows_per_call - 1)
        chunk_range = f"{start_col}{chunk_start}:{end_col}{chunk_end}"
        result = mcp_call(
            "get_range",
            {"nodeId": node_id, "sheetId": sheet_id, "range": chunk_range},
        )
        if not result.get("success"):
            return result
        expected_rows = chunk_end - chunk_start + 1
        raw_values = result.get("values")
        raw_display = result.get("displayValues")
        if isinstance(raw_values, list):
            has_values = True
        if isinstance(raw_display, list):
            has_display = True
        combined_values.extend(_pad(_rows(result, "values"), expected_rows))
        combined_display.extend(_pad(_rows(result, "displayValues"), expected_rows))
        last_result = result

    output = dict(last_result)
    output["success"] = True
    if has_values:
        output["values"] = combined_values
    else:
        output.pop("values", None)
    if has_display:
        output["displayValues"] = combined_display
    else:
        output.pop("displayValues", None)
    output["range"] = address
    output["chunked"] = True
    return output
