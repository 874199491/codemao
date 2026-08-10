"""Fetch CRM NCT exam data and write it to the DingTalk learning workbook.

The DingTalk write path intentionally goes through the local MCP helper
(`build_service_todo.mcp_call`) to keep all DingTalk operations on the MCP path.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from build_service_todo import mcp_call
from teacher_workbench_config import (
    data_prefix,
    learning_sheet_target,
    load_workbench_config,
    script_config,
)
from learning_sheet_schema import column_letter, required_column


WORKSPACE = Path(__file__).resolve().parents[1]
DATA = WORKSPACE / "data"
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
CRM_CONFIG_PATH = DATA / "new-class-group-send-cancel-config.json"
SHEET_NAME = "NCT考级"
LEARNING_CARD_HEADER = "是否有购买年卡"
LEARNING_CARD_HEADER_ALIASES = (
    LEARNING_CARD_HEADER,
    "是否购买年卡",
    "是否购买NCT年卡",
    "是否购买NCT年卡/权益卡",
    "是否购买权益卡",
    "是否有购买权益卡",
)

HEADERS = [
    "学生ID",
    "学生姓名",
    "上课时间",
    "是否购买NCT年卡",
    "NCT年卡状态",
    "权益卡可用次数",
    "上次报考等级",
    "上次考试时间",
    "上次报名状态",
    "上次考试状态",
    "上次考试结果",
    "下一次推荐报考等级",
    "NCT ID",
]


def load_crm_module():
    spec = importlib.util.spec_from_file_location("crm_nct", CRM_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load CRM module: {CRM_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def refresh_crm_cookies() -> Path:
    port = int(load_workbench_config().get("chrome_debug_port") or 9223)
    completed = subprocess.run(
        [
            "node",
            str(COOKIE_EXPORT),
            "--port",
            str(port),
            "--out",
            str(COOKIE_PATH),
        ],
        cwd=WORKSPACE,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**dict(os.environ), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    if completed.returncode != 0:
        raise RuntimeError("无法从 Chrome 导出 CRM Cookie：\n" + completed.stdout[-2000:])
    return COOKIE_PATH


def ts_to_text(value: Any) -> str:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if parsed <= 0:
        return ""
    return datetime.fromtimestamp(parsed).strftime("%Y-%m-%d %H:%M")


def card_state_text(value: Any) -> str:
    raw = "" if value is None else str(value)
    return {
        "0": "未购买/无可用年卡",
        "1": "已购买/可用",
        "2": "已使用/不可用",
        "3": "已过期",
    }.get(raw, raw)


def has_nct_card(row: dict[str, Any]) -> str:
    state = "" if row.get("privilegeCardState") is None else str(row.get("privilegeCardState"))
    if state in {"1", "已购买", "可用"}:
        return "是"
    if state in {"0", "2", "3"}:
        return "否"
    try:
        count = int(row.get("userReceiveAbleCount") or 0)
    except (TypeError, ValueError):
        count = 0
    return "是" if count > 0 else "否"


def fetch_nct_rows(profile: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    crm = load_crm_module()
    config = crm.read_json(CRM_CONFIG_PATH)
    config["cookies_file"] = str(refresh_crm_cookies())
    client = crm.CrmClient(config)

    fetched_summary: list[dict[str, Any]] = []
    by_user: dict[str, dict[str, str]] = {}
    for class_item in profile.get("classes") or []:
        class_id = int(class_item.get("class_id") or 0)
        if class_id <= 0:
            continue
        label = str(class_item.get("label") or class_id)
        page = 1
        total = 0
        class_count = 0
        while True:
            payload = {
                "classId": class_id,
                "deviceCheck": "",
                "relateNctState": "",
                "levelId": "",
                "examStatus": "",
                "examResult": "",
                "privilegeCardState": "",
                "page": page,
                "limit": 100,
            }
            response = client.post(f"{client.lbk_base}/nct/userList", payload)
            if response.get("success") is not True and response.get("code") != 200:
                raise RuntimeError(f"NCT userList failed for class {class_id}: {response}")
            data = response.get("data") or {}
            items = data.get("items") or []
            total = int(data.get("total") or total or 0)
            class_count += len(items)
            for item in items:
                student_id = str(item.get("userId") or "").strip()
                if not student_id or student_id in by_user:
                    continue
                by_user[student_id] = {
                    "学生ID": student_id,
                    "学生姓名": str(item.get("childName") or ""),
                    "上课时间": label,
                    "是否购买NCT年卡": has_nct_card(item),
                    "NCT年卡状态": card_state_text(item.get("privilegeCardState")),
                    "权益卡可用次数": str(
                        item.get("userReceiveAbleCount")
                        if item.get("userReceiveAbleCount") is not None
                        else ""
                    ),
                    "上次报考等级": str(item.get("levelName") or ""),
                    "上次考试时间": ts_to_text(item.get("examinationBeginTime")),
                    "上次报名状态": str(item.get("registerStatus") or ""),
                    "上次考试状态": str(item.get("examStatus") or ""),
                    "上次考试结果": str(item.get("examResult") or ""),
                    "下一次推荐报考等级": str(item.get("recommendLevelName") or ""),
                    "NCT ID": str(item.get("nctId") or ""),
                }
            if not items or len(items) < payload["limit"] or class_count >= total:
                break
            page += 1
            time.sleep(0.1)
        fetched_summary.append(
            {"class_id": class_id, "label": label, "total": total, "fetched": class_count}
        )
    return list(by_user.values()), fetched_summary


def learning_roster(profile: dict[str, Any]) -> dict[str, dict[str, str]]:
    target = learning_sheet_target(profile)
    schema = profile.get("learning_sheet_schema", {}).get("columns", {})
    id_i = int(schema.get("student_id", {}).get("index") or 1) - 1
    name_i = int(schema.get("student_name", {}).get("index") or 2) - 1
    class_i = int(schema.get("class_time", {}).get("index") or 3) - 1
    max_i = max(id_i, name_i, class_i)

    result = mcp_call(
        "get_range",
        {
            "nodeId": target["node_id"],
            "sheetId": target["sheet_id"],
            "range": target["range"],
        },
    )
    values = result.get("displayValues") or result.get("values") or []
    roster: dict[str, dict[str, str]] = {}
    for row in values[1:]:
        padded = list(row) + [""] * max(0, max_i + 1 - len(row))
        student_id = str(padded[id_i] or "").strip()
        if not student_id:
            continue
        roster[student_id] = {
            "学生ID": student_id,
            "学生姓名": str(padded[name_i] or "").strip(),
            "上课时间": str(padded[class_i] or "").strip(),
        }
    return roster


def learning_sheet_values(profile: dict[str, Any]) -> tuple[dict[str, str], list[list[Any]]]:
    target = learning_sheet_target(profile)
    result = mcp_call(
        "get_range",
        {
            "nodeId": target["node_id"],
            "sheetId": target["sheet_id"],
            "range": target["range"],
        },
    )
    values = result.get("displayValues") or result.get("values") or []
    return target, values


def find_or_create_learning_card_column(
    target: dict[str, str], values: list[list[Any]]
) -> tuple[int, str]:
    if not values:
        raise RuntimeError("学情表为空，无法同步 NCT 年卡列")
    headers = [str(value or "").strip() for value in values[0]]
    normalized = {"".join(header.split()).lower(): index for index, header in enumerate(headers)}
    for alias in LEARNING_CARD_HEADER_ALIASES:
        key = "".join(alias.split()).lower()
        if key in normalized:
            index = normalized[key]
            return index, column_letter(index + 1)

    occupied = [index for index, header in enumerate(headers, start=1) if header]
    next_index = max(occupied, default=len(headers)) + 1
    column = column_letter(next_index)
    result = mcp_call(
        "set_cell_range",
        {
            "nodeId": target["node_id"],
            "sheetId": target["sheet_id"],
            "rangeAddress": f"{column}1",
            "cells": [[{"type": "text", "text": LEARNING_CARD_HEADER}]],
        },
    )
    if not result.get("success"):
        raise RuntimeError(f"无法创建学情表列 {LEARNING_CARD_HEADER}: {result}")
    return next_index - 1, column


def sync_learning_card_column(
    profile: dict[str, Any], rows: list[dict[str, str]]
) -> dict[str, Any]:
    target, values = learning_sheet_values(profile)
    if len(values) <= 1:
        return {"synced": 0, "checked": 0, "column": None}
    headers = [str(value or "").strip() for value in values[0]]
    id_index = required_column(headers, profile, "student_id")
    card_index, card_column = find_or_create_learning_card_column(target, values)
    card_by_id = {
        str(row.get("学生ID") or "").strip(): row.get("是否购买NCT年卡") == "是"
        for row in rows
        if str(row.get("学生ID") or "").strip()
    }

    checkbox_cells: list[list[dict[str, Any]]] = []
    synced = 0
    checked = 0
    for row in values[1:]:
        padded = list(row) + [""] * max(0, id_index + 1 - len(row))
        student_id = str(padded[id_index] or "").strip()
        has_card = bool(student_id and card_by_id.get(student_id))
        if student_id:
            synced += 1
            checked += 1 if has_card else 0
        checkbox_cells.append([{"dataValidation": {"type": "checkbox", "checked": has_card}}])

    if not checkbox_cells:
        return {"synced": 0, "checked": 0, "column": card_column}

    for start in range(0, len(checkbox_cells), 120):
        chunk = checkbox_cells[start : start + 120]
        first_row = start + 2
        last_row = first_row + len(chunk) - 1
        result = mcp_call(
            "set_cell_range",
            {
                "nodeId": target["node_id"],
                "sheetId": target["sheet_id"],
                "rangeAddress": f"{card_column}{first_row}:{card_column}{last_row}",
                "cells": chunk,
            },
        )
        if not result.get("success"):
            raise RuntimeError(
                f"无法同步学情表 {LEARNING_CARD_HEADER} {card_column}{first_row}:{card_column}{last_row}: {result}"
            )
    return {
        "synced": synced,
        "checked": checked,
        "column": card_column,
        "column_index": card_index + 1,
    }


def merge_rows(
    roster: dict[str, dict[str, str]], nct_rows: list[dict[str, str]]
) -> list[dict[str, str]]:
    nct_by_id = {row["学生ID"]: row for row in nct_rows if row.get("学生ID")}
    merged: list[dict[str, str]] = []
    for student_id, base in roster.items():
        row = dict(nct_by_id.get(student_id) or {})
        output = {header: row.get(header, "") for header in HEADERS}
        output["学生ID"] = student_id
        output["学生姓名"] = base.get("学生姓名") or row.get("学生姓名", "")
        output["上课时间"] = base.get("上课时间", "")
        output["是否购买NCT年卡"] = row.get("是否购买NCT年卡") or "未查询到"
        merged.append(output)

    for student_id, row in nct_by_id.items():
        if student_id in roster:
            continue
        output = {header: row.get(header, "") for header in HEADERS}
        merged.append(output)
    return merged


def rows_to_csv(rows: list[dict[str, str]]) -> str:
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=HEADERS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def locate_or_create_sheet(node_id: str) -> str:
    result = mcp_call("get_all_sheets", {"nodeId": node_id})
    sheets = result.get("sheets") or result.get("value") or result.get("data") or []
    for sheet in sheets:
        if isinstance(sheet, dict) and sheet.get("name") == SHEET_NAME:
            return str(sheet.get("sheetId") or sheet.get("id"))
    created = mcp_call("create_sheet", {"nodeId": node_id, "name": SHEET_NAME})
    if created.get("success") is not True:
        raise RuntimeError(f"无法创建 {SHEET_NAME}: {created}")
    return str(created.get("sheetId") or created.get("id"))


def main() -> int:
    profile = script_config()
    prefix = data_prefix(profile)
    target = learning_sheet_target(profile)
    node_id = target["node_id"]

    nct_rows, fetch_summary = fetch_nct_rows(profile)
    roster = learning_roster(profile)
    rows = merge_rows(roster, nct_rows)
    csv_text = rows_to_csv(rows)

    DATA.mkdir(parents=True, exist_ok=True)
    csv_path = DATA / f"{prefix}-nct-exam-sheet.csv"
    json_path = DATA / f"{prefix}-nct-exam-sheet.json"
    csv_path.write_text(csv_text, encoding="utf-8-sig")
    json_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "fetch_summary": fetch_summary,
                "learning_roster": len(roster),
                "nct_rows": len({row["学生ID"] for row in nct_rows}),
                "sheet_rows": len(rows),
                "matched": sum(1 for student_id in roster if student_id in {r["学生ID"] for r in nct_rows}),
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    sheet_id = locate_or_create_sheet(node_id)
    # Keep the sheet itself, but replace the data region.
    mcp_call(
        "clear_range",
        {
            "nodeId": node_id,
            "sheetId": sheet_id,
            "range": "A1:S1200",
            "type": "all",
        },
    )
    write = mcp_call(
        "set_range_from_csv",
        {
            "nodeId": node_id,
            "sheetId": sheet_id,
            "startCell": "A1",
            "allowOverwrite": True,
            "csv": csv_text,
        },
    )
    if write.get("success") is not True:
        raise RuntimeError(f"写入 {SHEET_NAME} 失败：{write}")
    learning_card = sync_learning_card_column(profile, rows)
    verify = mcp_call(
        "get_range_as_csv",
        {"nodeId": node_id, "sheetId": sheet_id, "range": "A1:S6"},
    )
    print(
        json.dumps(
            {
                "sheet_name": SHEET_NAME,
                "sheet_id": sheet_id,
                "range": write.get("a1Notation"),
                "csv": str(csv_path),
                "json": str(json_path),
                "learning_roster": len(roster),
                "nct_rows": len({row["学生ID"] for row in nct_rows}),
                "sheet_rows": len(rows),
                "learning_card_column": learning_card.get("column"),
                "learning_card_synced": learning_card.get("synced"),
                "learning_card_checked": learning_card.get("checked"),
                "matched": sum(
                    1
                    for student_id in roster
                    if student_id in {row["学生ID"] for row in nct_rows}
                ),
                "preview": verify.get("csv", "")[:1200],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
