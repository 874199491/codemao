#!/usr/bin/env python3
"""Sync one week's group-solitaire checkboxes to the 0724 learning sheet."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_service_todo import mcp_call
from learning_sheet_schema import optional_column, required_column, required_week_column
from teacher_workbench_config import learning_sheet_target, script_config


CONFIG = script_config()
TARGET = learning_sheet_target(CONFIG)
NODE_ID = TARGET["node_id"]
SHEET_ID = TARGET["sheet_id"]
READ_RANGE = TARGET.get("range") or "A1:AZ300"
MANUAL_MATCHES = {
    "紫苏之若若": "1379004581",
    "张阳": "1391564184",
    "子韬": "1613004950",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--solitaire", type=Path, action="append", required=True)
    parser.add_argument(
        "--class-time",
        action="append",
        default=[],
        help="Only update rows whose class-time value matches this label.",
    )
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args()


def checked(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "是"}


def checkbox_cell(value: bool) -> dict[str, object]:
    return {"dataValidation": {"type": "checkbox", "checked": value}}


def column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def normalize_header(value: object) -> str:
    return "".join(character for character in str(value).strip().lower() if not character.isspace())


def header_index(headers: list[str], *candidates: str) -> int:
    normalized = [normalize_header(value) for value in headers]
    for candidate in candidates:
        value = normalize_header(candidate)
        if value in normalized:
            return normalized.index(value)
    raise RuntimeError(f"找不到列：{' / '.join(candidates)}；当前表头：{headers}")


def consecutive_batches(changes: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    ordered = sorted(changes, key=lambda item: int(item["row"]))
    batches: list[list[dict[str, object]]] = []
    for change in ordered:
        if not batches:
            batches.append([change])
            continue
        row_number = int(change["row"])
        previous_row = int(batches[-1][-1]["row"])
        if row_number == previous_row + 1:
            batches[-1].append(change)
        else:
            batches.append([change])
    return batches


def main() -> int:
    args = parse_args()
    if args.week < 1:
        raise RuntimeError("周次必须大于等于 1")

    ids: set[str] = set()
    unresolved: set[str] = set()
    source_summary: list[dict[str, object]] = []
    for path in args.solitaire:
        payload = json.loads(path.read_text(encoding="utf-8"))
        groups = payload.get("groups") or []
        if not groups:
            raise RuntimeError(f"{path.name} 没有找到对应的企微群，已停止写入")
        rows = payload.get("rows") or []
        for item in rows:
            user_id = str(item.get("userId") or "").strip()
            if user_id:
                ids.add(user_id)
                continue
            nickname = str(item.get("wechatName") or "").strip()
            mapped = MANUAL_MATCHES.get(nickname)
            if mapped:
                ids.add(mapped)
            elif nickname:
                unresolved.add(nickname)
        source_summary.append(
            {
                "file": str(path),
                "groups": len(groups),
                "messages": int(payload.get("uniqueSolitaireSenderCount") or 0),
                "matched": int(payload.get("matchedCount") or 0),
                "review": int(payload.get("reviewCount") or 0),
            }
        )

    total_messages = sum(int(source["messages"]) for source in source_summary)
    if total_messages == 0:
        raise RuntimeError(
            f"W{args.week} 接龙抓取结果为 0，已停止写入，原有勾选保持不变"
        )
    if not ids:
        preview = sorted(unresolved)[:30]
        suffix = "……" if len(unresolved) > len(preview) else ""
        raise RuntimeError(
            f"W{args.week} 接龙记录未匹配到任何学生ID，已停止写入，"
            "通常是 CRM 学员名单缓存不是当前老师/当前班期，或企微昵称映射缺失。"
            "请先刷新 CRM 学员名单或重新生成老师配置后再重试。"
            f"待人工核对昵称示例：{preview}{suffix}"
        )

    result = mcp_call(
        "get_range",
        {"nodeId": NODE_ID, "sheetId": SHEET_ID, "range": READ_RANGE},
    )
    if not result.get("success"):
        raise RuntimeError(f"无法读取 0724 学情表：{result}")
    values = result.get("values") or result.get("displayValues") or []
    if not values:
        raise RuntimeError("0724 学情表为空")
    headers = [str(value).strip() for value in values[0]]
    user_id_index = required_column(headers, CONFIG, "student_id")
    name_index = required_column(headers, CONFIG, "student_name")
    solitaire_index = required_week_column(headers, CONFIG, args.week, "solitaire")
    solitaire_column = column_letter(solitaire_index + 1)
    class_times = {value.strip() for value in args.class_time if value.strip()}
    class_time_index = (
        optional_column(headers, CONFIG, "class_time")
        if class_times
        else None
    )

    changes: list[dict[str, object]] = []
    before_count = 0
    after_count = 0
    learning_ids: set[str] = set()
    for row_number, row in enumerate(values[1:], start=2):
        padded = list(row) + [""] * (len(headers) - len(row))
        user_id = str(padded[user_id_index]).strip()
        if not user_id:
            continue
        if class_time_index is not None and str(padded[class_time_index]).strip() not in class_times:
            continue
        learning_ids.add(user_id)
        old_value = checked(padded[solitaire_index])
        new_value = old_value or user_id in ids
        before_count += int(old_value)
        after_count += int(new_value)
        if old_value != new_value:
            changes.append(
                {
                    "row": row_number,
                    "userId": user_id,
                    "name": str(padded[name_index]).strip(),
                    "old": old_value,
                    "new": new_value,
                }
            )

    unknown_ids = sorted(ids - learning_ids)

    summary = {
        "week": args.week,
        "classTimes": sorted(class_times),
        "sources": source_summary,
        "learningRows": len(learning_ids),
        "solitaireStudents": after_count,
        "beforeChecked": before_count,
        "plannedChanges": len(changes),
        "unresolvedNicknames": sorted(unresolved),
        "ignoredIdsOutsideLearningSheet": unknown_ids,
    }
    if args.check_only:
        print(json.dumps({"checkOnly": True, **summary}, ensure_ascii=False, indent=2))
        return 0

    if not changes:
        print(
            json.dumps(
                {
                    **summary,
                    "changedCells": 0,
                    "verifiedChecked": after_count,
                    "changes": [],
                    "readback": "skipped-no-changes",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    write_batches = consecutive_batches(changes)
    for batch in write_batches:
        first_row = int(batch[0]["row"])
        last_row = int(batch[-1]["row"])
        range_address = (
            f"{solitaire_column}{first_row}"
            if first_row == last_row
            else f"{solitaire_column}{first_row}:{solitaire_column}{last_row}"
        )
        write = mcp_call(
            "set_cell_range",
            {
                "nodeId": NODE_ID,
                "sheetId": SHEET_ID,
                "rangeAddress": range_address,
                "cells": [[checkbox_cell(bool(change["new"]))] for change in batch],
            },
        )
        if not write.get("success"):
            raise RuntimeError(f"无法更新 {range_address}：{write}")

    verify = mcp_call(
        "get_range",
        {
            "nodeId": NODE_ID,
            "sheetId": SHEET_ID,
            "range": f"{solitaire_column}2:{solitaire_column}{len(values)}",
        },
    )
    if not verify.get("success"):
        raise RuntimeError(f"无法校验 0724 学情表：{verify}")
    verified_count = 0
    verify_rows = verify.get("values") or verify.get("displayValues") or []
    for index, row in enumerate(verify_rows):
        original = values[index + 1] if index + 1 < len(values) else []
        padded_original = list(original) + [""] * (len(headers) - len(original))
        if not str(padded_original[user_id_index]).strip():
            continue
        if class_time_index is not None and str(padded_original[class_time_index]).strip() not in class_times:
            continue
        cell_value = row[0] if row else ""
        verified_count += int(checked(cell_value))
    if verified_count != after_count:
        raise RuntimeError(
            f"接龙校验不一致：勾选数 {verified_count}/{after_count}"
        )

    print(
        json.dumps(
            {
                **summary,
                "changedCells": len(changes),
                "writeBatches": len(write_batches),
                "verifiedChecked": verified_count,
                "changes": changes,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
