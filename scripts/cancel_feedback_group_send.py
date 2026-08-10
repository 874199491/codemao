#!/usr/bin/env python3
"""Cancel CRM enterprise-WeChat feedback group-send tasks from saved send results."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from build_service_todo import mcp_call
from teacher_workbench_config import data_path, data_prefix, script_config, wecom_config
from teacher_workbench_config import learning_sheet_target


WORKSPACE = Path(__file__).resolve().parents[1]
DATA = WORKSPACE / "data"
SCRIPTS = WORKSPACE / "scripts"
SKILL_CANCEL = (
    WORKSPACE
    / "skills"
    / "codemao-makeup-reminder"
    / "scripts"
    / "cancel_makeup_reminder.py"
)
COOKIE_EXPORT = (
    WORKSPACE
    / "skills"
    / "codemao-makeup-reminder"
    / "scripts"
    / "export_crm_cookies_from_chrome.mjs"
)
COOKIE_PATH = DATA / "crm-cookies.json"
BASE_CANCEL_CONFIG = DATA / "new-class-group-send-cancel-config.json"
CONFIG_PROFILE = script_config()
PREFIX = data_prefix(CONFIG_PROFILE)
FEEDBACK_SHEET_NAME = "课后学情反馈"
STATUS_HEADER = "是否已反馈"
STUDENT_ID_HEADER = "学生ID"
WEEK_HEADER = "周次"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--all-matches", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    return parser.parse_args()


def configured_path(pattern_or_path: str, *, week: int) -> Path:
    raw = str(pattern_or_path or "").format(prefix=PREFIX, week=week)
    path = Path(raw)
    return path if path.is_absolute() else WORKSPACE / path


def default_result_path(week: int) -> Path:
    wecom = wecom_config(CONFIG_PROFILE)
    pattern = str(
        wecom.get("send_result_pattern")
        or f"data/{PREFIX}-week{{week}}-feedback-send-result.json"
    )
    return configured_path(pattern, week=week)


def real_class_lookup() -> dict[int, dict[str, Any]]:
    path = data_path("completion_classes_csv", CONFIG_PROFILE)
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    lookup: dict[int, dict[str, Any]] = {}
    for row in rows:
        class_id = int(row.get("class_id") or row.get("classId") or 0)
        term_id = int(row.get("term_id") or row.get("termId") or 0)
        if class_id <= 0 or term_id <= 0:
            continue
        lookup[class_id] = {
            "name": row.get("class_name") or row.get("className") or str(class_id),
            "term_id": term_id,
            "class_id": class_id,
        }
    if not lookup:
        raise RuntimeError(f"No valid class_id/term_id rows in {path}")
    return lookup


def build_cancel_config() -> Path:
    if not BASE_CANCEL_CONFIG.exists():
        raise RuntimeError(f"Cannot find CRM cancel config: {BASE_CANCEL_CONFIG}")
    config = json.loads(BASE_CANCEL_CONFIG.read_text(encoding="utf-8"))
    config["cookies_file"] = str(refresh_crm_cookies())
    config["classes"] = list(real_class_lookup().values())
    profile_crm = CONFIG_PROFILE.get("crm") if isinstance(CONFIG_PROFILE.get("crm"), dict) else {}
    if int(profile_crm.get("class_pool_id") or 0) > 0:
        config["class_pool_id"] = int(profile_crm["class_pool_id"])
    path = DATA / f"{PREFIX}-feedback-cancel-config-latest.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def refresh_crm_cookies() -> Path:
    COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
    from teacher_workbench_config import load_workbench_config

    port = int(load_workbench_config().get("chrome_debug_port") or 9223)
    command = [
        "node",
        str(COOKIE_EXPORT),
        "--port",
        str(port),
        "--out",
        str(COOKIE_PATH),
    ]
    completed = subprocess.run(
        command,
        cwd=WORKSPACE,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**dict(os.environ), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "无法从 Chrome 导出 CRM Cookie。请确认看板 Chrome 已打开并登录 CRM。\n"
            + completed.stdout[-2000:]
        )
    print(completed.stdout.strip(), flush=True)
    return COOKIE_PATH


def load_cancel_targets(result_path: Path) -> tuple[int, list[str], dict[str, str]]:
    if not result_path.exists():
        raise RuntimeError(f"找不到反馈发送结果文件：{result_path}")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    course_id = int(payload.get("course_id") or 0)
    if course_id <= 0:
        raise RuntimeError(f"发送结果中缺少 course_id：{result_path}")
    ids = [
        str(item.get("student_id")).strip()
        for item in payload.get("results") or []
        if item.get("created") is True and str(item.get("student_id") or "").strip()
    ]
    hashes = {
        str(item.get("student_id")).strip(): str(item.get("message_sha256") or "").strip()
        for item in payload.get("results") or []
        if item.get("created") is True
        and str(item.get("student_id") or "").strip()
        and str(item.get("message_sha256") or "").strip()
    }
    unique_ids = list(dict.fromkeys(ids))
    if not unique_ids:
        raise RuntimeError(
            f"发送结果里没有 created=true 的群发任务，无法自动取消：{result_path}"
        )
    return course_id, unique_ids, hashes


def column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def locate_feedback_sheet(node_id: str) -> str:
    result = mcp_call("get_all_sheets", {"nodeId": node_id})
    sheets = result.get("sheets") or result.get("value") or result.get("data") or []
    for sheet in sheets:
        if isinstance(sheet, dict) and sheet.get("name") == FEEDBACK_SHEET_NAME:
            return str(sheet.get("sheetId") or sheet.get("id"))
    raise RuntimeError(f"找不到钉钉表格：{FEEDBACK_SHEET_NAME}")


def unmark_feedback_status(week: int, student_ids: list[str]) -> dict[str, Any]:
    """Uncheck feedback status for canceled send targets.

    This rewrites the feedback sheet data region once, then restores checkbox cells.
    It is intentionally heavier than cell-by-cell writes, but avoids DingTalk checkbox
    cells keeping a stale TRUE display value after only changing validation metadata.
    """
    target_ids = {str(value).strip() for value in student_ids if str(value).strip()}
    if not target_ids:
        return {"matched": 0, "unchecked": 0, "already_false": 0}
    target = learning_sheet_target(CONFIG_PROFILE)
    node_id = target["node_id"]
    sheet_id = locate_feedback_sheet(node_id)
    result = mcp_call(
        "get_range",
        {"nodeId": node_id, "sheetId": sheet_id, "range": "A1:P1200"},
    )
    values = result.get("displayValues") or result.get("values") or []
    if not values:
        raise RuntimeError(f"{FEEDBACK_SHEET_NAME} 为空，无法取消反馈标记")
    headers = [str(value).strip() for value in values[0]]
    id_index = headers.index(STUDENT_ID_HEADER)
    status_index = headers.index(STATUS_HEADER)
    week_index = headers.index(WEEK_HEADER) if WEEK_HEADER in headers else None
    width = len(headers)
    target_week = f"W{week}"

    rows: list[list[str]] = []
    matched = 0
    unchecked = 0
    already_false = 0
    for row in values[1:]:
        padded = [str(value or "").strip() for value in (list(row) + [""] * max(0, width - len(row)))[:width]]
        if not any(padded):
            continue
        row_week = str(padded[week_index]).strip() if week_index is not None else "W1"
        student_id = str(padded[id_index]).strip()
        if student_id in target_ids and row_week.upper() == target_week.upper():
            matched += 1
            old = str(padded[status_index]).strip().upper()
            if old in {"TRUE", "1", "YES", "是"}:
                unchecked += 1
            else:
                already_false += 1
            padded[status_index] = "FALSE"
        rows.append(padded)

    if matched == 0:
        return {
            "sheet_id": sheet_id,
            "matched": 0,
            "unchecked": 0,
            "already_false": 0,
        }

    import io

    stream = io.StringIO()
    csv.writer(stream, lineterminator="\n").writerows([headers, *rows])
    last_column = column_letter(width)
    clear = mcp_call(
        "clear_range",
        {"nodeId": node_id, "sheetId": sheet_id, "range": f"A:{last_column}"},
    )
    if clear.get("success") is False:
        raise RuntimeError(f"清空 {FEEDBACK_SHEET_NAME} 失败：{clear}")
    write = mcp_call(
        "set_range_from_csv",
        {
            "nodeId": node_id,
            "sheetId": sheet_id,
            "startCell": "A1",
            "csv": stream.getvalue(),
            "allowOverwrite": True,
        },
    )
    if write.get("success") is not True:
        raise RuntimeError(f"写回 {FEEDBACK_SHEET_NAME} 失败：{write}")

    status_column = column_letter(status_index + 1)
    for start in range(0, len(rows), 100):
        chunk = rows[start : start + 100]
        first_row = start + 2
        last_row = first_row + len(chunk) - 1
        cells = [
            [
                {
                    "dataValidation": {
                        "type": "checkbox",
                        "checked": str(row[status_index]).strip().upper() == "TRUE",
                    }
                }
            ]
            for row in chunk
        ]
        checkbox = mcp_call(
            "set_cell_range",
            {
                "nodeId": node_id,
                "sheetId": sheet_id,
                "rangeAddress": f"{status_column}{first_row}:{status_column}{last_row}",
                "cells": cells,
            },
        )
        if checkbox.get("success") is not True:
            raise RuntimeError(
                f"恢复复选框失败 {status_column}{first_row}:{status_column}{last_row}: {checkbox}"
            )
    return {
        "sheet_id": sheet_id,
        "matched": matched,
        "unchecked": unchecked,
        "already_false": already_false,
    }


def mark_result_invalidated(result_path: Path, week: int, student_ids: list[str], unmark_summary: dict[str, Any]) -> None:
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["invalidated_by_cancel"] = True
    payload["canceled_at"] = datetime.now().isoformat(timespec="seconds")
    payload["canceled_week"] = week
    payload["canceled_student_ids"] = student_ids
    payload["feedback_status_unmark"] = unmark_summary
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    result_path = args.result or default_result_path(args.week)
    course_id, student_ids, hashes = load_cancel_targets(result_path)
    ids_path = DATA / f"{PREFIX}-week{args.week}-feedback-cancel-ids.txt"
    ids_path.write_text("\n".join(student_ids) + "\n", encoding="utf-8")
    hashes_path = DATA / f"{PREFIX}-week{args.week}-feedback-cancel-hashes.json"
    hashes_path.write_text(json.dumps(hashes, ensure_ascii=False, indent=2), encoding="utf-8")
    cancel_config = build_cancel_config()
    command = [
        "py",
        "-3.10",
        str(SKILL_CANCEL),
        "--config",
        str(cancel_config),
        "--ids",
        str(ids_path),
        "--target-hashes",
        str(hashes_path),
        "--course-id",
        str(course_id),
        "--page-size",
        str(args.page_size),
    ]
    if args.all_matches:
        command.append("--all-matches")
    if args.continue_on_error:
        command.append("--continue-on-error")
    if args.execute:
        command.append("--execute")

    print(
        json.dumps(
            {
                "week": args.week,
                "mode": "execute" if args.execute else "dry-run",
                "result": str(result_path),
                "course_id": course_id,
                "student_count": len(student_ids),
                "ids_file": str(ids_path),
                "hash_guard_count": len(hashes),
                "hash_guard_file": str(hashes_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    completed = subprocess.run(
        command,
        cwd=SKILL_CANCEL.parent,
        text=True,
        env={**dict(os.environ), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    if args.execute:
        unmark_summary = unmark_feedback_status(args.week, student_ids)
        mark_result_invalidated(result_path, args.week, student_ids, unmark_summary)
        print(
            json.dumps(
                {
                    "feedback_status_unmarked": True,
                    "invalidated_send_result": str(result_path),
                    **unmark_summary,
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
    return completed.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
