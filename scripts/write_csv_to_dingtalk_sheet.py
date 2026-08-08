#!/usr/bin/env python3
"""Write a CSV file into a DingTalk spreadsheet sheet via the existing MCP endpoint."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


WORKSPACE = Path(__file__).resolve().parents[1]
SYNC_PY = WORKSPACE / "skills" / "codemao-course-data" / "sync.py"


def read_rows(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.reader(file))


def extract_sync_constant(name: str) -> str:
    text = SYNC_PY.read_text(encoding="utf-8", errors="ignore")
    match = re.search(rf'{name}\s*=\s*"([^"]+)"', text)
    if not match:
        raise RuntimeError(f"Could not find {name} in {SYNC_PY}")
    return match.group(1)


def mcp_call(name: str, arguments: dict[str, object]) -> dict[str, object]:
    url = extract_sync_constant("MCP_URL")
    token = extract_sync_constant("ACCESS_TOKEN")
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
        "id": 1,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": "Bearer " + token,
    }
    session = requests.Session()
    session.trust_env = False
    for attempt in range(1, 4):
        try:
            response = session.post(url, json=payload, headers=headers, timeout=90)
            response.raise_for_status()
            result = response.json()
            if "error" in result:
                raise RuntimeError(str(result["error"]))
            content = result.get("result", {}).get("content", [])
            if content:
                text = content[0].get("text", "{}")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"text": text}
            return result.get("result", {})
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 * attempt)
    raise RuntimeError(f"MCP call failed: {name}")


def col_letter(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(ord("A") + rem) + result
    return result


def formula_text(value: str) -> str:
    if not value:
        return ""
    if all(ord(ch) < 128 for ch in value):
        return value
    parts: list[str] = []
    ascii_buffer: list[str] = []

    def flush_ascii() -> None:
        if ascii_buffer:
            text = "".join(ascii_buffer).replace('"', '""')
            parts.append(f'"{text}"')
            ascii_buffer.clear()

    for ch in value:
        if ord(ch) < 128:
            ascii_buffer.append(ch)
        else:
            flush_ascii()
            parts.append(f"UNICHAR({ord(ch)})")
    flush_ascii()
    return "=" + "&".join(parts)


def ensure_sheet(node_id: str, sheet_name: str) -> None:
    data = mcp_call("get_all_sheets", {"nodeId": node_id})
    sheets = data.get("sheets") or data.get("value") or data.get("data") or []
    exists = any(
        str(item.get("name") or item.get("title") or item.get("sheetName") or "") == sheet_name
        or str(item.get("sheetId") or item.get("id") or "") == sheet_name
        for item in sheets
        if isinstance(item, dict)
    )
    if not exists:
        result = mcp_call("create_sheet", {"nodeId": node_id, "name": sheet_name})
        print(f"created_sheet {json.dumps(result, ensure_ascii=False)[:400]}")


def write_sheet(node_id: str, sheet_name: str, rows: list[list[str]]) -> None:
    if not rows:
        raise RuntimeError("CSV has no rows")
    ensure_sheet(node_id, sheet_name)
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    end_col = col_letter(width - 1)
    clear_height = max(1000, len(normalized) + 100)
    clear_result = mcp_call(
        "update_range",
        {
            "nodeId": node_id,
            "sheetId": sheet_name,
            "rangeAddress": f"A1:{end_col}{clear_height}",
            "values": [[""] * width for _ in range(clear_height)],
            "format": "plain_text",
        },
    )
    print("clear", bool(clear_result.get("success", clear_result)))

    formulas = [[formula_text(str(cell)) for cell in row] for row in normalized]
    chunk_size = 80
    for start in range(0, len(formulas), chunk_size):
        values = formulas[start : start + chunk_size]
        first_row = start + 1
        last_row = start + len(values)
        result = mcp_call(
            "update_range",
            {
                "nodeId": node_id,
                "sheetId": sheet_name,
                "rangeAddress": f"A{first_row}:{end_col}{last_row}",
                "values": values,
            },
        )
        print("write", first_row, last_row, bool(result.get("success", result)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--node-id", required=True, help="DingTalk node id or alidocs URL")
    parser.add_argument("--sheet-name", required=True)
    args = parser.parse_args()

    rows = read_rows(Path(args.csv))
    write_sheet(args.node_id, args.sheet_name, rows)
    print(json.dumps({"csv": args.csv, "sheet_name": args.sheet_name, "rows": len(rows) - 1}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
