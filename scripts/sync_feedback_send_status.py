#!/usr/bin/env python3
"""Mark DingTalk feedback rows only after enterprise-WeChat delivery succeeds."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from build_service_todo import mcp_call
from dingtalk_range_reader import get_complete_range
from teacher_workbench_config import (
    data_path,
    data_prefix,
    learning_sheet_target,
    load_workbench_config,
    script_config,
    wecom_config,
)


WORKSPACE = Path(__file__).resolve().parents[1]
DATA = WORKSPACE / "data"
CONFIG_PROFILE = script_config()
PREFIX = data_prefix(CONFIG_PROFILE)
NODE_ID = learning_sheet_target(CONFIG_PROFILE)["node_id"]
CONFIG_PATH = DATA / "new-class-group-send-cancel-config.json"
CRM_MODULE_PATH = (
    WORKSPACE
    / "skills"
    / "codemao-makeup-reminder"
    / "scripts"
    / "create_makeup_reminder.py"
)
COOKIE_EXPORT = (
    WORKSPACE
    / "skills"
    / "codemao-makeup-reminder"
    / "scripts"
    / "export_crm_cookies_from_chrome.mjs"
)
COOKIE_PATH = DATA / "crm-cookies.json"


def load_crm_module():
    spec = importlib.util.spec_from_file_location("crm_feedback_status", CRM_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 CRM 模块：{CRM_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def refresh_crm_cookies() -> Path:
    COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--course-id", type=int)
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--created-only",
        action="store_true",
        help="Mark DingTalk as soon as CRM tasks are created, without waiting for final WeCom send status.",
    )
    return parser.parse_args()


def feedback_sheet_name(week: int) -> str:
    return "课后学情反馈"


def feedback_result_path(week: int) -> Path:
    wecom = wecom_config(CONFIG_PROFILE)
    pattern = str(
        wecom.get("send_result_pattern")
        or f"data/{PREFIX}-week{{week}}-feedback-send-result.json"
    )
    raw = pattern.format(prefix=PREFIX, week=week)
    path = Path(raw)
    return path if path.is_absolute() else WORKSPACE / path


def course_id_for_week(week: int, payload: dict[str, Any]) -> int:
    stored = int(payload.get("course_id") or 0)
    if stored > 0:
        return stored
    course_file = DATA / f"{PREFIX}-course-{week * 2}-feedback.json"
    if not course_file.exists():
        raise RuntimeError(f"找不到课程数据：{course_file}")
    course_payload = json.loads(course_file.read_text(encoding="utf-8"))
    course_id = int(course_payload.get("courseId") or 0)
    if course_id <= 0:
        raise RuntimeError(f"无法从 {course_file.name} 确认课程 ID")
    return course_id


def real_classes() -> list[dict[str, Any]]:
    path = data_path("completion_classes_csv", CONFIG_PROFILE)
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    classes: list[dict[str, Any]] = []
    for row in rows:
        class_id = int(row.get("class_id") or row.get("classId") or 0)
        term_id = int(row.get("term_id") or row.get("termId") or 0)
        if class_id <= 0 or term_id <= 0:
            continue
        classes.append(
            {
                "slot": row.get("class_name") or row.get("className") or str(class_id),
                "term_id": term_id,
                "class_id": class_id,
            }
        )
    if not classes:
        raise RuntimeError(f"No valid class_id/term_id rows in {path}")
    return classes


def record_content_hash(record: dict[str, Any]) -> str:
    contents = record.get("msgContents") or []
    text = "\n".join(
        str(item.get("resourceContent") or "")
        for item in contents
        if isinstance(item, dict)
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def successful_ids_from_crm(
    course_id: int,
    targets: dict[int, dict[str, Any]],
    page_size: int,
) -> tuple[set[int], dict[int, dict[str, Any]]]:
    crm = load_crm_module()
    config = crm.read_json(CONFIG_PATH)
    config["cookies_file"] = str(refresh_crm_cookies())
    config["classes"] = [
        {
            "name": item["slot"],
            "term_id": item["term_id"],
            "class_id": item["class_id"],
        }
        for item in real_classes()
    ]
    client = crm.CrmClient(config)
    unmatched = set(targets)
    matched: dict[int, dict[str, Any]] = {}
    for class_item in config["classes"]:
        response = client.post(
            f"{client.lbk_base}/qwb/send/message/record/page",
            {
                "termId": int(class_item["term_id"]),
                "classId": int(class_item["class_id"]),
                "pageIndex": 1,
                "pageSize": page_size,
            },
        )
        if response.get("success") is not True and response.get("code") != 200:
            raise RuntimeError(f"无法读取 CRM 群发记录：{response}")
        records = (response.get("data") or {}).get("items") or []
        for record in records:
            if int(record.get("courseId") or 0) != course_id:
                continue
            choose_ids = {int(value) for value in record.get("chooseUserList") or []}
            candidates = sorted(unmatched.intersection(choose_ids))
            if not candidates:
                continue
            content_hash = record_content_hash(record)
            for user_id in candidates:
                expected_hash = str(targets[user_id].get("message_sha256") or "")
                if expected_hash and content_hash != expected_hash:
                    continue
                success_ids = {
                    int(value)
                    for value in record.get("successReceiveUserIdList") or []
                }
                created_success_ids = {
                    int(value)
                    for value in record.get("successUserIdList") or []
                }
                fail_ids = {
                    int(value)
                    for value in record.get("failReceiveUserIdList") or []
                }
                matched[user_id] = {
                    "recordId": int(record["id"]),
                    "createdTime": record.get("createdTime"),
                    "sent": user_id in success_ids or user_id in created_success_ids,
                    "sentSource": (
                        "successReceiveUserIdList"
                        if user_id in success_ids
                        else "successUserIdList"
                        if user_id in created_success_ids
                        else ""
                    ),
                    "failed": user_id in fail_ids,
                }
                unmatched.remove(user_id)
    return {
        user_id for user_id, item in matched.items() if item["sent"]
    }, matched


def locate_sheet(sheet_name: str) -> str:
    result = mcp_call("get_all_sheets", {"nodeId": NODE_ID})
    sheets = result.get("sheets") or result.get("value") or result.get("data") or []
    for sheet in sheets:
        if isinstance(sheet, dict) and sheet.get("name") == sheet_name:
            return str(sheet.get("sheetId") or sheet.get("id"))
    raise RuntimeError(f"找不到钉钉表格：{sheet_name}")


def mark_feedback(
    sheet_id: str,
    successful_ids: set[int],
    week: int,
    check_only: bool,
) -> tuple[int, list[int], list[int]]:
    result = get_complete_range(
        mcp_call,
        node_id=NODE_ID,
        sheet_id=sheet_id,
        range_address="A1:P1200",
    )
    values = result.get("displayValues") or result.get("values") or []
    if not values:
        raise RuntimeError("课后学情反馈表为空")
    headers = [str(value).strip() for value in values[0]]
    try:
        id_index = headers.index("学生ID")
        status_index = headers.index("是否已反馈")
    except ValueError as error:
        raise RuntimeError(f"课后学情反馈表缺少必要列：{headers}") from error
    week_index = headers.index("周次") if "周次" in headers else None
    target_week = f"W{week}"

    rows_to_mark: list[tuple[int, int]] = []
    found_ids: set[int] = set()
    for row_number, row in enumerate(values[1:], start=2):
        padded = list(row) + [""] * (len(headers) - len(row))
        raw_id = str(padded[id_index]).strip()
        if not raw_id:
            continue
        row_week = str(padded[week_index]).strip() if week_index is not None else "W1"
        if row_week.upper() != target_week.upper():
            continue
        user_id = int(raw_id)
        if user_id not in successful_ids:
            continue
        found_ids.add(user_id)
        already_checked = str(padded[status_index]).strip().lower() in {
            "true",
            "1",
            "yes",
            "是",
        }
        if not already_checked:
            rows_to_mark.append((row_number, user_id))
    missing_ids = sorted(successful_ids - found_ids)
    if missing_ids:
        print(
            "WARN: 发送成功但不在对应周反馈表中，已跳过："
            + "、".join(str(value) for value in missing_ids),
            flush=True,
        )
    if check_only:
        return len(rows_to_mark), [user_id for _, user_id in rows_to_mark], missing_ids

    column = ""
    index = status_index + 1
    while index:
        index, remainder = divmod(index - 1, 26)
        column = chr(65 + remainder) + column
    ranges: list[tuple[int, int]] = []
    if rows_to_mark:
        row_numbers = [row_number for row_number, _ in rows_to_mark]
        start = previous = row_numbers[0]
        for row_number in row_numbers[1:]:
            if row_number == previous + 1:
                previous = row_number
            else:
                ranges.append((start, previous))
                start = previous = row_number
        ranges.append((start, previous))
    for first_row, last_row in ranges:
        cells = [
            [{"dataValidation": {"type": "checkbox", "checked": True}}]
            for _ in range(first_row, last_row + 1)
        ]
        write = mcp_call(
            "set_cell_range",
            {
                "nodeId": NODE_ID,
                "sheetId": sheet_id,
                "rangeAddress": f"{column}{first_row}:{column}{last_row}",
                "cells": cells,
            },
        )
        if not write.get("success"):
            raise RuntimeError(f"无法更新 {column}{first_row}:{column}{last_row}：{write}")
    return len(rows_to_mark), [user_id for _, user_id in rows_to_mark], missing_ids


def main() -> int:
    args = parse_args()
    result_path = args.result or feedback_result_path(args.week)
    if not result_path.exists():
        print(
            json.dumps(
                {
                    "week": args.week,
                    "skipped": True,
                    "reason": "没有上一次反馈发送记录",
                },
                ensure_ascii=False,
            )
        )
        return 0
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if payload.get("invalidated_by_cancel") or payload.get("canceled_at"):
        print(
            json.dumps(
                {
                    "week": args.week,
                    "skipped": True,
                    "reason": "反馈发送记录已取消/作废，不再同步为已反馈",
                    "result": str(result_path),
                    "canceled_at": payload.get("canceled_at"),
                },
                ensure_ascii=False,
            )
        )
        return 0
    results = payload.get("results") or []
    targets = {
        int(item["student_id"]): item
        for item in results
        if item.get("created") is True and item.get("student_id")
    }
    if not targets:
        print(
            json.dumps(
                {
                    "week": args.week,
                    "skipped": True,
                    "reason": "上一次记录中没有已创建的反馈任务",
                },
                ensure_ascii=False,
            )
        )
        return 0

    course_id = args.course_id or course_id_for_week(args.week, payload)
    if args.created_only:
        successful_ids = set(targets)
        matched = {
            user_id: {
                "sent": True,
                "source": "created_only",
                "failed": False,
            }
            for user_id in targets
        }
    else:
        successful_ids, matched = successful_ids_from_crm(
            course_id,
            targets,
            args.page_size,
        )
    sheet_name = feedback_sheet_name(args.week)
    sheet_id = locate_sheet(sheet_name)
    changed, changed_ids, missing_ids = mark_feedback(
        sheet_id,
        successful_ids,
        args.week,
        args.check_only,
    )
    summary = {
        "synced_at": datetime.now().isoformat(timespec="seconds"),
        "check_only": args.check_only,
        "week": args.week,
        "course_id": course_id,
        "mode": "created_only" if args.created_only else "crm_send_status",
        "targets": len(targets),
        "matched_records": len(matched),
        "sent_successfully": len(successful_ids),
        "marked_now": changed,
        "missing_in_feedback_sheet": len(missing_ids),
        "pending_or_failed": len(targets) - len(successful_ids),
        "marked_student_ids": [str(value) for value in changed_ids],
        "missing_student_ids": [str(value) for value in missing_ids],
        "sheet_name": sheet_name,
    }
    if not args.check_only:
        payload["course_id"] = course_id
        payload["last_status_sync"] = summary
        result_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
