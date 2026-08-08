#!/usr/bin/env python3
"""Append live-absence records from CSV to the DingTalk sheet."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

import requests

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

WORKSPACE = Path(__file__).resolve().parents[1]
COURSE_DATA_SKILL = WORKSPACE / "skills" / "codemao-course-data"
DEFAULT_SHEET_NAME = "\u76f4\u64ad\u672a\u53c2\u52a0\u8bb0\u5f55"

HEADERS = [
    "记录时间",
    "课程周次",
    "直播课节",
    "直播ID",
    "直播标题",
    "直播开始时间",
    "直播类型",
    "班级名称",
    "班级ID",
    "学员ID",
    "学生姓名",
    "微信昵称",
    "是否到播",
    "直播观看时长秒",
    "总观看时长秒",
    "回放时长秒",
    "是否评论",
    "评论次数",
    "互动次数",
    "首次进入直播间时间",
    "最后离开直播间时间",
    "备注",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def mcp_credentials() -> tuple[str, str]:
    sync = (COURSE_DATA_SKILL / "sync.py").read_text(encoding="utf-8", errors="ignore")
    url = re.search(r'MCP_URL\s*=\s*"([^"]+)"', sync).group(1)
    token = re.search(r'ACCESS_TOKEN\s*=\s*"([^"]+)"', sync).group(1)
    return url, token


def mcp_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    url, token = mcp_credentials()
    session = requests.Session()
    session.trust_env = False
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
    response = session.post(url, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    result = response.json()
    if "error" in result:
        raise SystemExit(f"MCP error: {result['error']}")
    content = result.get("result", {}).get("content", [])
    if content:
        text = content[0].get("text", "{}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
    return result.get("result", {})


def existing_keys(node_id: str, sheet_name: str) -> set[tuple[str, str]]:
    try:
        data = mcp_call("get_range", {"nodeId": node_id, "sheetId": sheet_name, "range": "A:V"})
    except Exception as error:
        print(f"Warning: could not read existing records, append will not dedupe: {error}")
        return set()

    values = data.get("values") or data.get("data") or []
    if len(values) < 2:
        return set()

    header = [str(value).strip() for value in values[0]]
    try:
        live_idx = header.index("直播ID")
        user_idx = header.index("学员ID")
    except ValueError:
        return set()

    keys: set[tuple[str, str]] = set()
    for row in values[1:]:
        if len(row) <= max(live_idx, user_idx):
            continue
        live_id = str(row[live_idx]).strip()
        user_id = str(row[user_idx]).strip()
        if live_id and user_id:
            keys.add((live_id, user_id))
    return keys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="CSV from fetch_live_absence_from_crm.mjs")
    parser.add_argument("--sheet-name", default=DEFAULT_SHEET_NAME)
    parser.add_argument("--no-dedupe", action="store_true")
    args = parser.parse_args()

    config = read_json(COURSE_DATA_SKILL / "config.json")
    node_id = config["dingtalk"]["node_id"]

    with Path(args.csv).open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    seen = set() if args.no_dedupe else existing_keys(node_id, args.sheet_name)
    values: list[list[str]] = []
    skipped = 0
    for row in rows:
        key = (str(row.get("直播ID", "")).strip(), str(row.get("学员ID", "")).strip())
        if not args.no_dedupe and key in seen:
            skipped += 1
            continue
        seen.add(key)
        values.append([row.get(header, "") for header in HEADERS])

    if not values:
        print(f"No new live absence records to append. skipped={skipped}")
        return 0

    mcp_call("append_rows", {"nodeId": node_id, "sheetId": args.sheet_name, "values": values})
    print(f"Appended {len(values)} live absence record row(s) to DingTalk sheet: {args.sheet_name}")
    if skipped:
        print(f"Skipped duplicate row(s): {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
