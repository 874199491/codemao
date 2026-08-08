"""Row-keyed incremental writes for DingTalk spreadsheet sheets."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any


McpCall = Callable[[str, dict[str, Any]], dict[str, Any]]
CellFormatter = Callable[[str], str]


def column_letter(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def normalize_row(row: Sequence[object], width: int) -> list[str]:
    return [str(row[index]).strip() if index < len(row) and row[index] is not None else "" for index in range(width)]


def key_for(row: Sequence[str], indexes: Sequence[int]) -> tuple[str, ...]:
    return tuple(row[index].strip() if index < len(row) else "" for index in indexes)


def sync_rows_by_key(
    *,
    call: McpCall,
    node_id: str,
    sheet_id: str,
    rows: list[list[str]],
    key_indexes: Sequence[int],
    formula: CellFormatter,
    ignore_indexes: Sequence[int] = (),
    require_all_key_parts: bool = True,
    audit_file: Path | None = None,
) -> dict[str, int]:
    """Upsert sheet rows by stable keys and clear only stale keyed rows.

    Values are compared as visible text before formula encoding, which keeps
    unchanged Chinese cells from being rewritten on every refresh.
    """

    if len(rows) < 1:
        raise ValueError("Rows must include a header row")

    width = len(rows[0])
    if not width:
        raise ValueError("Rows must include at least one column")
    if not key_indexes:
        raise ValueError("At least one key column is required")

    end_col = column_letter(width - 1)
    existing_data = call(
        "get_range",
        {"nodeId": node_id, "sheetId": sheet_id, "range": f"A:{end_col}"},
    )
    raw_values = existing_data.get("values") or existing_data.get("displayValues") or existing_data.get("data") or []
    current = [normalize_row(row, width) for row in raw_values if isinstance(row, list)]
    desired = [normalize_row(row, width) for row in rows]

    desired_by_key: dict[tuple[str, ...], list[str]] = {}
    for row in desired[1:]:
        key = key_for(row, key_indexes)
        if (require_all_key_parts and not all(key)) or (not require_all_key_parts and not any(key)):
            raise ValueError(f"Missing incremental key for row: {row}")
        if key in desired_by_key:
            raise ValueError(f"Duplicate incremental key: {key}")
        desired_by_key[key] = row

    existing_by_key: dict[tuple[str, ...], int] = {}
    duplicate_rows: set[int] = set()
    last_nonempty_row = 1
    blank_rows: list[int] = []
    for row_number, row in enumerate(current[1:], start=2):
        if any(row):
            last_nonempty_row = row_number
        else:
            blank_rows.append(row_number)
            continue
        key = key_for(row, key_indexes)
        if (require_all_key_parts and not all(key)) or (not require_all_key_parts and not any(key)):
            continue
        if key in existing_by_key:
            duplicate_rows.add(row_number)
        else:
            existing_by_key[key] = row_number

    stale_rows = {
        row_number
        for key, row_number in existing_by_key.items()
        if key not in desired_by_key
    }
    stale_rows.update(duplicate_rows)
    available_rows = sorted(row for row in blank_rows if row <= last_nonempty_row) + sorted(stale_rows)
    next_row = last_nonempty_row + 1
    updates: dict[int, list[str]] = {}
    changes: list[dict[str, object]] = []
    inserted = 0
    updated = 0

    if not current or current[0] != desired[0]:
        updates[1] = desired[0]

    for key, row in desired_by_key.items():
        row_number = existing_by_key.get(key)
        if row_number is None:
            if available_rows:
                row_number = available_rows.pop(0)
                stale_rows.discard(row_number)
            else:
                row_number = next_row
                next_row += 1
            updates[row_number] = row
            changes.append({"operation": "inserted", "row": row_number, "key": key, "before": None, "after": row})
            inserted += 1
            continue
        existing_row = current[row_number - 1] if row_number <= len(current) else [""] * width
        changed = any(
            index not in ignore_indexes and existing_row[index] != row[index]
            for index in range(width)
        )
        if changed:
            updates[row_number] = row
            changes.append({"operation": "updated", "row": row_number, "key": key, "before": existing_row, "after": row})
            updated += 1

    blank = [""] * width
    for row_number in sorted(stale_rows):
        updates[row_number] = blank
        changes.append({"operation": "removed", "row": row_number, "key": key_for(current[row_number - 1], key_indexes), "before": current[row_number - 1], "after": None})

    for row_number in sorted(updates):
        values = [[formula(str(cell)) for cell in updates[row_number]]]
        result = call(
            "update_range",
            {
                "nodeId": node_id,
                "sheetId": sheet_id,
                "rangeAddress": f"A{row_number}:{end_col}{row_number}",
                "values": values,
            },
        )
        if not bool(result.get("success", result)):
            raise RuntimeError(f"DingTalk update failed for row {row_number}: {json.dumps(result, ensure_ascii=False)[:500]}")

    stats = {"updated": updated, "inserted": inserted, "removed": len(stale_rows), "written_rows": len(updates)}
    if audit_file:
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "sheet_id": sheet_id,
            "stats": stats,
            "changes": changes,
        }
        with audit_file.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")
    print("incremental", json.dumps(stats, ensure_ascii=False))
    return stats
