#!/usr/bin/env python3
"""Audit and optionally sync student class-time labels without damaging checkboxes.

This script compares the learning sheet's current "上课时间" with the latest CRM
roster.  In apply mode it updates only the changed "上课时间" cells, then rewrites
checkbox columns as DingTalk checkbox cells instead of plain TRUE/FALSE text.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from build_service_todo import mcp_call
from learning_sheet_schema import required_column
from teacher_workbench_config import (
    class_mappings,
    data_path,
    data_prefix,
    learning_sheet_target,
    script_config,
)


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


WORKSPACE = Path(__file__).resolve().parents[1]
DATA_DIR = WORKSPACE / "data"
FETCH_ROSTER = DATA_DIR / "fetch-new-class-student-list.mjs"
CONFIG = script_config()
PREFIX = data_prefix(CONFIG)
TARGET = learning_sheet_target(CONFIG)
ROSTER_JSON = data_path("students_json", CONFIG)
ROSTER_CSV = data_path("roster_csv", CONFIG)
DEFAULT_REPORT = DATA_DIR / f"{PREFIX}-class-time-audit-latest.json"
CLASS_ID_TO_LABEL = dict(class_mappings(CONFIG))
CLASS_TIME_ORDER = tuple(label for _, label in class_mappings(CONFIG))
CHECKBOX_EXACT_HEADERS = {"重点关注", "请假", "是否电话跟进"}
CLASS_TIME_FIXED_ORDER = ("周五晚", "周六午", "周六晚")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="核对 CRM 班级归属与钉钉学情表上课时间；默认只检查，不写入。"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="将确认到的时间段差异写入学情表，并把复选框列恢复为钉钉复选框。",
    )
    parser.add_argument(
        "--skip-crm-refresh",
        action="store_true",
        help="使用本地现有 CRM 名单，不重新抓取。主要用于离线检查和测试。",
    )
    parser.add_argument("--roster-json", type=Path, default=ROSTER_JSON)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--checkbox-chunk-size",
        type=int,
        default=80,
        help="恢复复选框时每批写入的行数，默认 80。",
    )
    parser.add_argument(
        "--no-sort",
        action="store_true",
        help="apply 模式下不重新排序学情表。默认会按周五晚、周六午、周六晚排序。",
    )
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def refresh_crm_roster() -> None:
    print(f"刷新 CRM 学员名单：{FETCH_ROSTER}")
    subprocess.run(["node", str(FETCH_ROSTER)], cwd=WORKSPACE, check=True)


def normalize(value: object) -> str:
    return "".join(str(value or "").strip().lower().split())


def text_cell(value: str) -> dict[str, object]:
    return {"type": "text", "text": value}


def column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def column_number(label: str) -> int:
    number = 0
    for char in str(label or "").strip().upper():
        if not ("A" <= char <= "Z"):
            continue
        number = number * 26 + ord(char) - 64
    return number


def header_index(headers: list[str], *candidates: str) -> int:
    normalized_headers = [normalize(value) for value in headers]
    for candidate in candidates:
        normalized = normalize(candidate)
        if normalized in normalized_headers:
            return normalized_headers.index(normalized)
    raise ValueError(candidates[0] if candidates else "")


def normalize_class_time(value: object) -> str:
    return "".join(str(value or "").strip().split())


def class_time_rank(value: object) -> int:
    normalized = normalize_class_time(value)
    for index, label in enumerate(CLASS_TIME_FIXED_ORDER):
        if normalized.startswith(normalize_class_time(label)):
            return index
    for index, label in enumerate(CLASS_TIME_ORDER):
        if normalized.startswith(normalize_class_time(label)):
            return 1000 + index
    return 1000 + len(CLASS_TIME_ORDER)


def sorted_class_time_order() -> list[str]:
    order = list(CLASS_TIME_FIXED_ORDER)
    for label in sorted(CLASS_TIME_ORDER, key=class_time_rank):
        if label not in order:
            order.append(label)
    return order


def desired_class_time(student: dict[str, Any]) -> str:
    try:
        class_id = int(student.get("realClassId") or student.get("classId") or 0)
    except (TypeError, ValueError):
        class_id = 0
    label = CLASS_ID_TO_LABEL.get(class_id, "")
    if label:
        return label

    # Fallback for future profiles whose config is incomplete.
    try:
        day = int(student.get("dayOfWeek") or 0)
    except (TypeError, ValueError):
        day = 0
    raw_time = str(student.get("classTime") or "").strip()
    try:
        hour = int(raw_time.split(":", 1)[0])
    except (TypeError, ValueError):
        hour = 0
    if day == 5:
        return "周五晚"
    if day == 6:
        return "周六午" if hour < 18 else "周六晚"
    return ""


def confirmed_refund_ids() -> set[str]:
    path = data_path("confirmed_refunded_json", CONFIG)
    if not path.exists():
        return set()
    payload = read_json(path)
    students = payload.get("students", []) if isinstance(payload, dict) else []
    return {
        str(student.get("userId") or "").strip()
        for student in students
        if isinstance(student, dict) and str(student.get("userId") or "").strip()
    }


def crm_refund_ids() -> set[str]:
    path = data_path("refunded_json", CONFIG)
    if not path.exists():
        return set()
    payload = read_json(path)
    items = payload.get("data", {}).get("items", []) if isinstance(payload, dict) else []
    return {
        str(item.get("userId") or item.get("user_id") or "").strip()
        for item in items
        if isinstance(item, dict)
        and str(item.get("userId") or item.get("user_id") or "").strip()
    }


def all_refund_ids() -> set[str]:
    return confirmed_refund_ids() | crm_refund_ids()


def crm_students_by_id(path: Path, refund_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"CRM 名单文件不存在：{path}")
    payload = read_json(path)
    items = payload.get("data", {}).get("items", []) if isinstance(payload, dict) else []
    if not items:
        raise RuntimeError(f"CRM 名单为空：{path}")

    result: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    ignored_class_ids: Counter[int] = Counter()
    for student in items:
        if not isinstance(student, dict):
            continue
        user_id = str(student.get("userId") or "").strip()
        if not user_id or user_id in refund_ids:
            continue
        try:
            class_id = int(student.get("realClassId") or student.get("classId") or 0)
        except (TypeError, ValueError):
            class_id = 0
        desired = desired_class_time(student)
        if class_id not in CLASS_ID_TO_LABEL and desired not in CLASS_TIME_ORDER:
            ignored_class_ids[class_id] += 1
            continue
        if user_id in result:
            duplicates.add(user_id)
        result[user_id] = student
    if duplicates:
        raise RuntimeError(f"CRM 名单存在重复学生ID：{sorted(duplicates)}")
    if not result:
        raise RuntimeError(f"CRM 名单没有匹配到当前配置班级：{sorted(CLASS_ID_TO_LABEL)}")
    return result


def write_roster_csv(crm_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    for user_id, student in sorted(
        crm_by_id.items(),
        key=lambda item: (
            class_time_rank(desired_class_time(item[1])),
            str(item[1].get("childName") or ""),
            int(item[0]) if item[0].isdigit() else 0,
        ),
    ):
        class_time = desired_class_time(student)
        rows.append(
            {
                "学生ID": user_id,
                "学生姓名": str(student.get("childName") or student.get("studentName") or "").strip(),
                "上课时间": class_time,
                "班级": str(student.get("className") or "").strip(),
            }
        )
    ROSTER_CSV.parent.mkdir(parents=True, exist_ok=True)
    with ROSTER_CSV.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=["学生ID", "学生姓名", "上课时间", "班级"])
        writer.writeheader()
        writer.writerows(rows)
    return {"path": str(ROSTER_CSV), "studentCount": len(rows)}


def read_learning_sheet() -> tuple[list[str], list[list[Any]]]:
    result = mcp_call(
        "get_range",
        {
            "nodeId": TARGET["node_id"],
            "sheetId": TARGET["sheet_id"],
            "range": TARGET["range"],
        },
    )
    if not result.get("success"):
        raise RuntimeError(f"无法读取学情表：{json.dumps(result, ensure_ascii=False)[:500]}")
    values = result.get("displayValues") or result.get("values") or []
    if not values:
        raise RuntimeError("学情表没有返回任何数据")
    headers = [str(value or "").strip() for value in values[0]]
    rows = [list(row) for row in values[1:] if isinstance(row, list)]
    return headers, rows


def active_student_rows(headers: list[str], rows: list[list[Any]]) -> list[int]:
    user_id_index = required_column(headers, CONFIG, "student_id")
    active: list[int] = []
    for index, row in enumerate(rows):
        padded = row + [""] * max(0, len(headers) - len(row))
        if str(padded[user_id_index] or "").strip():
            active.append(index)
    return active


def checkbox_headers(headers: list[str]) -> list[str]:
    result: list[str] = []
    for header in headers:
        clean = str(header or "").strip()
        if not clean:
            continue
        if clean in CHECKBOX_EXACT_HEADERS or clean.startswith("是否") or clean.endswith("接龙"):
            result.append(clean)
    return result


def checkbox_checked(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = normalize(value)
    if text in {"true", "1", "yes", "y", "是", "已勾选", "勾选", "√", "✓"}:
        return True
    if text in {"false", "0", "no", "n", "否", "未勾选", ""}:
        return False
    # DingTalk sometimes displays checked checkboxes as TRUE/FALSE strings.  If
    # a manual remark accidentally appears in a checkbox column, fail closed.
    raise RuntimeError(f"无法识别复选框值：{value!r}")


def checkbox_cell(checked: bool) -> dict[str, object]:
    return {"dataValidation": {"type": "checkbox", "checked": checked}}


def restore_checkbox_columns(
    headers: list[str],
    rows: list[list[Any]],
    chunk_size: int,
) -> dict[str, Any]:
    active_indexes = active_student_rows(headers, rows)
    if not active_indexes:
        return {"restored": False, "reason": "学情表没有学生行"}
    if active_indexes != list(range(active_indexes[0], active_indexes[-1] + 1)):
        raise RuntimeError("学生行不连续，停止批量恢复复选框")

    restored: dict[str, dict[str, int]] = {}
    first_row = active_indexes[0] + 2
    last_row = active_indexes[-1] + 2
    for header in checkbox_headers(headers):
        index = header_index(headers, header)
        values = [
            checkbox_checked(rows[row_index][index] if index < len(rows[row_index]) else "")
            for row_index in active_indexes
        ]
        column = column_letter(index + 1)
        for start in range(0, len(values), chunk_size):
            batch = values[start : start + chunk_size]
            row_start = first_row + start
            row_end = row_start + len(batch) - 1
            result = mcp_call(
                "set_cell_range",
                {
                    "nodeId": TARGET["node_id"],
                    "sheetId": TARGET["sheet_id"],
                    "rangeAddress": f"{column}{row_start}:{column}{row_end}",
                    "cells": [[checkbox_cell(value)] for value in batch],
                },
            )
            if not result.get("success"):
                raise RuntimeError(
                    f"恢复 {header} 复选框失败："
                    f"{json.dumps(result, ensure_ascii=False)[:500]}"
                )
        restored[header] = {
            "checked": sum(values),
            "unchecked": len(values) - sum(values),
        }

    return {
        "restored": True,
        "firstRow": first_row,
        "lastRow": last_row,
        "columns": restored,
    }


def target_range_last_row(default_last_row: int) -> int:
    match = re.search(r":\D+(\d+)\s*$", str(TARGET["range"]))
    if not match:
        return default_last_row
    return max(default_last_row, int(match.group(1)))


def target_range_last_column(default_last_column: int) -> int:
    match = re.search(r":([A-Z]+)\d+\s*$", str(TARGET["range"]), re.IGNORECASE)
    if not match:
        return default_last_column
    return max(default_last_column, column_number(match.group(1)))


def full_row_width(headers: list[str], rows: list[list[Any]]) -> int:
    last_header_column = max(
        (index + 1 for index, header in enumerate(headers) if str(header).strip()),
        default=len(headers),
    )
    widest_row = max((len(row) for row in rows if isinstance(row, list)), default=0)
    return target_range_last_column(max(last_header_column, widest_row, len(headers)))


def clear_extra_rows_after_students(
    headers: list[str],
    rows: list[list[Any]],
) -> dict[str, Any]:
    active_indexes = active_student_rows(headers, rows)
    if not active_indexes:
        return {"cleared": False, "reason": "学情表没有学生行"}

    last_student_row = active_indexes[-1] + 2
    last_read_row = len(rows) + 1
    last_target_row = target_range_last_row(last_read_row)
    clear_start = last_student_row + 1
    clear_end = max(last_read_row, last_target_row)
    if clear_start > clear_end:
        return {
            "cleared": False,
            "reason": "没有多余空行",
            "lastStudentRow": last_student_row,
        }

    last_column = column_letter(full_row_width(headers, rows))
    clear_range = f"A{clear_start}:{last_column}{clear_end}"
    result = mcp_call(
        "clear_range",
        {
            "nodeId": TARGET["node_id"],
            "sheetId": TARGET["sheet_id"],
            "range": clear_range,
        },
    )
    if not result.get("success"):
        raise RuntimeError(
            "清理学生名单后方多余空行失败："
            f"{json.dumps(result, ensure_ascii=False)[:500]}"
        )
    return {
        "cleared": True,
        "range": clear_range,
        "lastStudentRow": last_student_row,
        "clearStartRow": clear_start,
        "clearEndRow": clear_end,
    }


def build_audit(
    headers: list[str],
    rows: list[list[Any]],
    crm_by_id: dict[str, dict[str, Any]],
    refund_ids: set[str],
) -> dict[str, Any]:
    user_id_index = required_column(headers, CONFIG, "student_id")
    name_index = required_column(headers, CONFIG, "student_name")
    class_time_index = required_column(headers, CONFIG, "class_time")

    sheet_ids: set[str] = set()
    duplicate_sheet_ids: set[str] = set()
    changes: list[dict[str, Any]] = []
    refunded_sheet_rows: list[dict[str, Any]] = []
    sheet_class_counts: Counter[str] = Counter()
    desired_class_counts: Counter[str] = Counter()

    for row_number, row in enumerate(rows, start=2):
        padded = row + [""] * max(0, len(headers) - len(row))
        user_id = str(padded[user_id_index] or "").strip()
        if not user_id:
            continue
        if user_id in sheet_ids:
            duplicate_sheet_ids.add(user_id)
        sheet_ids.add(user_id)
        current = str(padded[class_time_index] or "").strip()
        if user_id in refund_ids:
            refunded_sheet_rows.append(
                {
                    "row": row_number,
                    "userId": user_id,
                    "name": str(padded[name_index] or "").strip(),
                    "classTime": current,
                }
            )
            continue
        sheet_class_counts[current] += 1

        student = crm_by_id.get(user_id)
        if student is None:
            continue
        desired = desired_class_time(student)
        if not desired:
            raise RuntimeError(f"CRM 学员 {user_id} 缺少可识别的上课时间")
        desired_class_counts[desired] += 1
        if normalize_class_time(current) == normalize_class_time(desired):
            continue
        changes.append(
            {
                "row": row_number,
                "userId": user_id,
                "name": str(padded[name_index] or student.get("childName") or "").strip(),
                "oldClassTime": current,
                "newClassTime": desired,
                "crmClassName": str(student.get("className") or "").strip(),
                "crmClassId": int(student.get("realClassId") or student.get("classId") or 0),
            }
        )

    if duplicate_sheet_ids:
        raise RuntimeError(f"学情表存在重复学生ID：{sorted(duplicate_sheet_ids)}")

    active_sheet_ids = sheet_ids.difference(refund_ids)
    missing_in_sheet = sorted(set(crm_by_id).difference(active_sheet_ids))
    missing_in_crm = sorted(active_sheet_ids.difference(crm_by_id))
    return {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "mode": "check-only",
        "profile": PREFIX,
        "target": {
            "nodeId": TARGET["node_id"],
            "sheetId": TARGET["sheet_id"],
            "range": TARGET["range"],
        },
        "classTimeColumn": column_letter(class_time_index + 1),
        "configuredClassTimes": list(CLASS_TIME_ORDER),
        "crmStudentCount": len(crm_by_id),
        "sheetStudentCount": len(active_sheet_ids),
        "matchedStudentCount": len(active_sheet_ids.intersection(crm_by_id)),
        "changeCount": len(changes),
        "changes": changes,
        "missingInSheet": [
            {
                "userId": user_id,
                "name": str(crm_by_id[user_id].get("childName") or "").strip(),
                "classTime": desired_class_time(crm_by_id[user_id]),
            }
            for user_id in missing_in_sheet
        ],
        "missingInCrm": missing_in_crm,
        "refundIds": sorted(refund_ids),
        "refundedSheetRows": refunded_sheet_rows,
        "refundedSheetRowCount": len(refunded_sheet_rows),
        "ignoredConfirmedRefunds": sorted(sheet_ids.intersection(refund_ids)),
        "sheetClassCounts": dict(sheet_class_counts),
        "desiredClassCounts": dict(desired_class_counts),
        "checkboxColumns": checkbox_headers(headers),
    }


def apply_changes(audit: dict[str, Any]) -> None:
    column = str(audit["classTimeColumn"])
    changes = list(audit["changes"])
    for change in changes:
        row_number = int(change["row"])
        result = mcp_call(
            "set_cell_range",
            {
                "nodeId": TARGET["node_id"],
                "sheetId": TARGET["sheet_id"],
                "rangeAddress": f"{column}{row_number}",
                "cells": [[text_cell(str(change["newClassTime"]))]],
            },
        )
        if not result.get("success"):
            raise RuntimeError(
                f"更新 {column}{row_number} 失败："
                f"{json.dumps(result, ensure_ascii=False)[:500]}"
            )


def contiguous_descending_groups(row_numbers: list[int]) -> list[tuple[int, int]]:
    groups: list[tuple[int, int]] = []
    for row_number in sorted(set(row_numbers), reverse=True):
        if not groups:
            groups.append((row_number, row_number))
            continue
        start, end = groups[-1]
        if row_number == start - 1:
            groups[-1] = (row_number, end)
        else:
            groups.append((row_number, row_number))
    return groups


def delete_refunded_rows(audit: dict[str, Any]) -> dict[str, Any]:
    refunded_rows = list(audit.get("refundedSheetRows") or [])
    row_numbers = [
        int(item["row"])
        for item in refunded_rows
        if isinstance(item, dict) and str(item.get("row") or "").isdigit()
    ]
    if not row_numbers:
        return {"deleted": False, "deletedRows": 0, "groups": []}

    groups = contiguous_descending_groups(row_numbers)
    deleted_groups: list[dict[str, int]] = []
    for start, end in groups:
        length = end - start + 1
        result = mcp_call(
            "delete_dimension",
            {
                "nodeId": TARGET["node_id"],
                "sheetId": TARGET["sheet_id"],
                "dimension": "ROWS",
                "position": str(start),
                "length": length,
            },
        )
        if not result.get("success"):
            raise RuntimeError(
                f"删除退费学生行 {start}:{end} 失败："
                f"{json.dumps(result, ensure_ascii=False)[:500]}"
            )
        deleted_groups.append({"start": start, "end": end, "length": length})
    return {
        "deleted": True,
        "deletedRows": len(row_numbers),
        "groups": deleted_groups,
        "students": refunded_rows,
    }


def initialize_learning_sheet_from_crm(
    headers: list[str],
    crm_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    user_id_index = required_column(headers, CONFIG, "student_id")
    name_index = required_column(headers, CONFIG, "student_name")
    class_time_index = required_column(headers, CONFIG, "class_time")
    selected_indexes = [user_id_index, name_index, class_time_index]
    first_index = min(selected_indexes)
    last_index = max(selected_indexes)
    first_column = column_letter(first_index + 1)
    last_column = column_letter(last_index + 1)

    def sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, str, int]:
        user_id, student = item
        class_time = desired_class_time(student)
        return (
            class_time_rank(class_time),
            str(student.get("childName") or ""),
            int(user_id) if user_id.isdigit() else 0,
        )

    rows: list[list[dict[str, object]]] = []
    for user_id, student in sorted(crm_by_id.items(), key=sort_key):
        class_time = desired_class_time(student)
        if not class_time:
            raise RuntimeError(f"CRM 学员 {user_id} 缺少可识别的上课时间")
        values = [""] * (last_index - first_index + 1)
        values[user_id_index - first_index] = user_id
        values[name_index - first_index] = str(student.get("childName") or "").strip()
        values[class_time_index - first_index] = class_time
        rows.append([text_cell(value) for value in values])

    if not rows:
        raise RuntimeError("CRM 名单为空，无法初始化学情表")
    row_start = 2
    row_end = row_start + len(rows) - 1
    result = mcp_call(
        "set_cell_range",
        {
            "nodeId": TARGET["node_id"],
            "sheetId": TARGET["sheet_id"],
            "rangeAddress": f"{first_column}{row_start}:{last_column}{row_end}",
            "cells": rows,
        },
    )
    if not result.get("success"):
        raise RuntimeError(
            "从 CRM 初始化学情表失败："
            f"{json.dumps(result, ensure_ascii=False)[:500]}"
        )
    return {
        "initialized": True,
        "rowStart": row_start,
        "rowEnd": row_end,
        "studentCount": len(rows),
        "range": f"{first_column}{row_start}:{last_column}{row_end}",
    }


def rewrite_active_rows_sorted(
    headers: list[str],
    rows: list[list[Any]],
    active_indexes: list[int],
    class_time_index: int,
) -> dict[str, Any]:
    if not active_indexes:
        return {"rewritten": False, "reason": "学情表没有学生行"}
    if active_indexes != list(range(active_indexes[0], active_indexes[-1] + 1)):
        raise RuntimeError("学生行不连续，停止兜底排序")

    width = full_row_width(headers, rows)
    first_row = active_indexes[0] + 2
    last_row = active_indexes[-1] + 2
    first_column = "A"
    last_column = column_letter(width)

    sortable_rows: list[tuple[int, int, list[Any]]] = []
    for position, row_index in enumerate(active_indexes):
        row = rows[row_index] if row_index < len(rows) else []
        padded = row + [""] * max(0, width - len(row))
        class_time = padded[class_time_index] if class_time_index < len(padded) else ""
        sortable_rows.append((class_time_rank(class_time), position, padded[:width]))

    sorted_rows = [item[2] for item in sorted(sortable_rows, key=lambda item: (item[0], item[1]))]
    result = mcp_call(
        "set_cell_range",
        {
            "nodeId": TARGET["node_id"],
            "sheetId": TARGET["sheet_id"],
            "rangeAddress": f"{first_column}{first_row}:{last_column}{last_row}",
            "cells": [
                [text_cell("" if value is None else str(value)) for value in row]
                for row in sorted_rows
            ],
        },
    )
    if not result.get("success"):
        raise RuntimeError(
            "兜底重排学生行失败："
            f"{json.dumps(result, ensure_ascii=False)[:500]}"
        )
    return {
        "rewritten": True,
        "range": f"{first_column}{first_row}:{last_column}{last_row}",
        "rowCount": len(sorted_rows),
    }


def verify_changes(audit: dict[str, Any]) -> dict[str, Any]:
    changes = list(audit["changes"])
    if not changes:
        return {"verified": True, "checkedCells": 0}

    headers, rows = read_learning_sheet()
    user_id_index = required_column(headers, CONFIG, "student_id")
    class_time_index = required_column(headers, CONFIG, "class_time")
    by_id: dict[str, str] = {}
    for row in rows:
        padded = row + [""] * max(0, len(headers) - len(row))
        user_id = str(padded[user_id_index] or "").strip()
        if user_id:
            by_id[user_id] = str(padded[class_time_index] or "").strip()

    mismatches: list[dict[str, str]] = []
    for change in changes:
        actual = by_id.get(str(change["userId"]), "")
        expected = str(change["newClassTime"])
        if normalize_class_time(actual) != normalize_class_time(expected):
            mismatches.append(
                {
                    "userId": str(change["userId"]),
                    "name": str(change.get("name") or ""),
                    "actual": actual,
                    "expected": expected,
                }
            )
    if mismatches:
        raise RuntimeError(f"上课时间回读不一致：{json.dumps(mismatches, ensure_ascii=False)[:500]}")
    return {"verified": True, "checkedCells": len(changes)}


def sort_learning_sheet() -> dict[str, Any]:
    headers, rows = read_learning_sheet()
    active_indexes = active_student_rows(headers, rows)
    if not active_indexes:
        return {"verified": True, "sorted": False, "reason": "学情表没有学生行"}
    if active_indexes != list(range(active_indexes[0], active_indexes[-1] + 1)):
        raise RuntimeError("学生行不连续，停止排序")

    class_time_index = required_column(headers, CONFIG, "class_time")
    user_id_index = required_column(headers, CONFIG, "student_id")
    ranks_before = [
        class_time_rank(rows[row_index][class_time_index] if class_time_index < len(rows[row_index]) else "")
        for row_index in active_indexes
    ]
    needs_sort = ranks_before != sorted(ranks_before)

    backup_path = ""
    if needs_sort:
        archive_dir = DATA_DIR / "archives"
        archive_dir.mkdir(parents=True, exist_ok=True)
        backup = archive_dir / (
            f"{PREFIX}-learning-sheet-before-class-time-sort-"
            + datetime.now().strftime("%Y%m%d-%H%M%S")
            + ".json"
        )
        backup.write_text(
            json.dumps({"headers": headers, "rows": rows}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        backup_path = str(backup)

        sort_width = full_row_width(headers, rows)
        helper_index = sort_width
        helper_column = column_letter(helper_index + 1)
        first_row = active_indexes[0] + 2
        last_row = active_indexes[-1] + 2

        helper_values = [["__class_time_sort_rank__"]]
        for row_index in active_indexes:
            class_time = (
                rows[row_index][class_time_index]
                if class_time_index < len(rows[row_index])
                else ""
            )
            helper_values.append([str(class_time_rank(class_time) + 1)])
        helper_write = mcp_call(
            "set_cell_range",
            {
                "nodeId": TARGET["node_id"],
                "sheetId": TARGET["sheet_id"],
                "rangeAddress": f"{helper_column}1:{helper_column}{last_row}",
                "cells": [[text_cell(value[0])] for value in helper_values],
            },
        )
        if not helper_write.get("success"):
            raise RuntimeError(
                "写入临时排序列失败："
                f"{json.dumps(helper_write, ensure_ascii=False)[:500]}"
            )

        result = mcp_call(
            "sort_range",
            {
                "nodeId": TARGET["node_id"],
                "sheetId": TARGET["sheet_id"],
                "range": f"A1:{helper_column}{last_row}",
                "sortKeys": [
                    {
                        "column": helper_column,
                        "ascending": True,
                    }
                ],
                "hasHeader": True,
            },
        )
        if not result.get("success"):
            raise RuntimeError(
                "重新排序学情表失败："
                f"{json.dumps(result, ensure_ascii=False)[:500]}"
            )
        clear_helper = mcp_call(
            "clear_range",
            {
                "nodeId": TARGET["node_id"],
                "sheetId": TARGET["sheet_id"],
                "range": f"{helper_column}1:{helper_column}{last_row}",
            },
        )
        if not clear_helper.get("success"):
            raise RuntimeError(
                "清理临时排序列失败："
                f"{json.dumps(clear_helper, ensure_ascii=False)[:500]}"
            )

    fallback_result: dict[str, Any] = {"rewritten": False}
    verify_headers, verify_rows = read_learning_sheet()
    verify_class_time_index = required_column(verify_headers, CONFIG, "class_time")
    verify_user_id_index = required_column(verify_headers, CONFIG, "student_id")
    verify_ranks: list[int] = []
    class_counts: Counter[str] = Counter()
    for row in verify_rows:
        padded = row + [""] * max(0, len(verify_headers) - len(row))
        if not str(padded[verify_user_id_index] or "").strip():
            continue
        class_time = str(padded[verify_class_time_index] or "").strip()
        verify_ranks.append(class_time_rank(class_time))
        class_counts[class_time] += 1
    if verify_ranks != sorted(verify_ranks):
        fallback_active_indexes = active_student_rows(verify_headers, verify_rows)
        fallback_result = rewrite_active_rows_sorted(
            verify_headers,
            verify_rows,
            fallback_active_indexes,
            verify_class_time_index,
        )
        verify_headers, verify_rows = read_learning_sheet()
        verify_class_time_index = required_column(verify_headers, CONFIG, "class_time")
        verify_user_id_index = required_column(verify_headers, CONFIG, "student_id")
        verify_ranks = []
        class_counts = Counter()
        for row in verify_rows:
            padded = row + [""] * max(0, len(verify_headers) - len(row))
            if not str(padded[verify_user_id_index] or "").strip():
                continue
            class_time = str(padded[verify_class_time_index] or "").strip()
            verify_ranks.append(class_time_rank(class_time))
            class_counts[class_time] += 1
        if verify_ranks != sorted(verify_ranks):
            raise RuntimeError(
                "排序回读校验失败：上课时间未按周五晚、周六午、周六晚排列（"
                + "、".join(sorted_class_time_order())
                + "）"
            )

    return {
        "verified": True,
        "sorted": needs_sort,
        "order": sorted_class_time_order(),
        "rowCount": len(verify_ranks),
        "classCounts": dict(class_counts),
        "backup": backup_path,
        "fallback": fallback_result,
    }


def main() -> int:
    args = parse_args()
    if args.checkbox_chunk_size <= 0:
        raise RuntimeError("--checkbox-chunk-size 必须大于 0")
    if not args.skip_crm_refresh:
        refresh_crm_roster()

    refund_ids = all_refund_ids()
    crm_by_id = crm_students_by_id(args.roster_json, refund_ids)
    roster_result = write_roster_csv(crm_by_id)
    headers, rows = read_learning_sheet()
    audit = build_audit(headers, rows, crm_by_id, refund_ids)
    init_result: dict[str, Any] | None = None
    if audit["crmStudentCount"] > 0 and audit["sheetStudentCount"] == 0:
        if not args.apply:
            raise RuntimeError(
                "学情表未识别到任何学生ID；检查模式已停止。若确认这是新空表，"
                "请使用 --apply，系统会从 CRM 初始化学生ID、姓名和上课时间。"
            )
        init_result = initialize_learning_sheet_from_crm(headers, crm_by_id)
        headers, rows = read_learning_sheet()
        audit = build_audit(headers, rows, crm_by_id, refund_ids)

    if args.apply:
        delete_result = delete_refunded_rows(audit)
        if delete_result.get("deleted"):
            headers, rows = read_learning_sheet()
            audit = build_audit(headers, rows, crm_by_id, refund_ids)
        apply_changes(audit)
        verify_result = verify_changes(audit)
        sort_result = (
            {"verified": True, "sorted": False, "skipped": True}
            if args.no_sort
            else sort_learning_sheet()
        )
        # Re-read after class-time writes and sorting, then force every checkbox column back
        # into real DingTalk checkbox cells.  This is the important guardrail:
        # no set_range_from_csv and no TRUE/FALSE text writes.
        latest_headers, latest_rows = read_learning_sheet()
        checkbox_result = restore_checkbox_columns(
            latest_headers,
            latest_rows,
            args.checkbox_chunk_size,
        )
        latest_headers, latest_rows = read_learning_sheet()
        extra_rows_result = clear_extra_rows_after_students(
            latest_headers,
            latest_rows,
        )
        audit["mode"] = "applied"
        if init_result:
            audit["initializeResult"] = init_result
        audit["updatedCells"] = audit["changeCount"]
        audit["rosterCsvResult"] = roster_result
        audit["deleteRefundedRowsResult"] = delete_result
        audit["verifyResult"] = verify_result
        audit["sortResult"] = sort_result
        audit["checkboxResult"] = checkbox_result
        audit["extraRowsResult"] = extra_rows_result
    else:
        audit["updatedCells"] = 0
        audit["rosterCsvResult"] = roster_result

    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    print(f"核对报告：{args.report_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
