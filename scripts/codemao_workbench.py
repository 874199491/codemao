#!/usr/bin/env python3
"""Small status helper for the distributable teacher workbench."""

from __future__ import annotations

import argparse
import json
import socket
from datetime import datetime
from pathlib import Path

from teacher_workbench_config import (
    class_mappings,
    data_path,
    data_prefix,
    learning_sheet_target,
    load_workbench_config,
    script_config,
)


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.25)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def row_count(path: Path) -> int:
    if not path.exists():
        return 0
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return 0
        if isinstance(payload, list):
            return len(payload)
        if isinstance(payload, dict):
            items = payload.get("data", {}).get("items") if isinstance(payload.get("data"), dict) else None
            if isinstance(items, list):
                return len(items)
        return 0
    try:
        return max(0, len(path.read_text(encoding="utf-8-sig").splitlines()) - 1)
    except OSError:
        return 0


def command_status(_: argparse.Namespace) -> int:
    profile = script_config()
    app_config = load_workbench_config()
    files = profile.get("files", {})
    file_status = {
        key: {
            "path": str(data_path(key, profile)),
            "exists": data_path(key, profile).exists(),
            "rows": row_count(data_path(key, profile)),
        }
        for key in files
    }
    summary = {
        "data_prefix": data_prefix(profile),
        "chrome_debug_port": int(app_config.get("chrome_debug_port") or 9223),
        "chrome_ready": is_port_open(int(app_config.get("chrome_debug_port") or 9223)),
        "dingtalk": learning_sheet_target(profile),
        "classes": [
            {"class_id": class_id, "label": label}
            for class_id, label in class_mappings(profile)
        ],
        "files": file_status,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="Check current workbench configuration.")
    status.set_defaults(func=command_status)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
