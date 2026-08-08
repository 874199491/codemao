#!/usr/bin/env python3
"""Build one week's invite follow-up rows and merge them into one shared sheet."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any

from build_service_todo import mcp_call
from learning_sheet_schema import optional_column, required_column, required_week_column
from teacher_workbench_config import data_prefix, learning_sheet_target, load_workbench_config, script_config
from week_context import context_for


WORKSPACE = Path(__file__).resolve().parents[1]
CONFIG = script_config()
PREFIX = data_prefix(CONFIG)
WORKBENCH_CONFIG = load_workbench_config()
TARGET = learning_sheet_target(CONFIG)
NODE_ID = TARGET["node_id"]
LEARNING_SHEET_ID = TARGET["sheet_id"]
READ_RANGE = TARGET["range"]
HEADERS = [
    "周次",
    "分类",
    "学生ID",
    "学生姓名",
    "上课时间",
    "是否接龙",
    "学情表请假",
    "邀约时间",
    "家长回复",
    "请假/未到原因",
    "备注",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--class-prefix", default="周五")
    parser.add_argument("--sheet-name", default=TARGET["invite_followup_sheet_name"])
    parser.add_argument("--port", type=int, default=int(WORKBENCH_CONFIG.get("chrome_debug_port") or 9223))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args()


def checked(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "是"}


def normalized(value: object) -> str:
    return "".join(str(value).strip().lower().split())


def header_index(headers: list[str], *candidates: str) -> int:
    values = [normalized(value) for value in headers]
    for candidate in candidates:
        target = normalized(candidate)
        if target in values:
            return values.index(target)
    raise RuntimeError(f"找不到列 {' / '.join(candidates)}；当前表头：{headers}")


def learning_students(week: int, class_prefix: str) -> list[dict[str, Any]]:
    result = mcp_call(
        "get_range",
        {"nodeId": NODE_ID, "sheetId": LEARNING_SHEET_ID, "range": READ_RANGE},
    )
    if not result.get("success"):
        raise RuntimeError(f"无法读取学情表：{result}")
    values = result.get("values") or result.get("displayValues") or []
    if not values:
        raise RuntimeError("学情表为空")
    headers = [str(value).strip() for value in values[0]]
    id_index = required_column(headers, CONFIG, "student_id")
    name_index = required_column(headers, CONFIG, "student_name")
    class_index = required_column(headers, CONFIG, "class_time")
    leave_index = optional_column(headers, CONFIG, "leave")
    reason_index = optional_column(
        headers,
        CONFIG,
        "leave_reason",
        "没有来参加直播/未完课原因",
        "请假原因",
        "没看直播/未到原因",
    )
    chain_index = required_week_column(headers, CONFIG, week, "solitaire")

    students: list[dict[str, Any]] = []
    for row in values[1:]:
        padded = list(row) + [""] * (len(headers) - len(row))
        user_id = str(padded[id_index]).strip()
        class_time = str(padded[class_index]).strip()
        if not user_id or not class_time.startswith(class_prefix):
            continue
        students.append(
            {
                "id": user_id,
                "name": str(padded[name_index]).strip(),
                "class_time": class_time,
                "leave": checked(padded[leave_index]) if leave_index is not None else False,
                "reason": str(padded[reason_index]).strip() if reason_index is not None else "",
                "chained": checked(padded[chain_index]),
            }
        )
    return students


def fetch_chat(
    student: dict[str, Any], port: int, chat_dir: Path
) -> tuple[str, str]:
    command = [
        "node",
        str(WORKSPACE / "scripts" / "fetch_parent_chat_from_crm.mjs"),
        "--port",
        str(port),
        "--user-id",
        student["id"],
        "--out-dir",
        str(chat_dir),
        "--limit",
        "500",
        "--months",
        "1",
    ]
    completed = subprocess.run(
        command,
        cwd=WORKSPACE,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=180,
    )
    if completed.returncode:
        return student["id"], (completed.stderr or completed.stdout).strip()[-1000:]
    return student["id"], ""


def all_messages(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    messages = [
        message
        for conversation in payload.get("conversations", [])
        for message in conversation.get("messages", [])
    ]
    return sorted(messages, key=lambda item: int(item.get("msgTime") or 0))


def invitation_anchor(
    messages: list[dict[str, Any]], cutoff_ms: int
) -> dict[str, Any] | None:
    outbound = [
        message
        for message in messages
        if int(message.get("flag", -1)) == 1
        and int(message.get("msgTime") or 0) >= cutoff_ms
    ]
    files = [
        message
        for message in outbound
        if str(message.get("msgType") or message.get("type") or "") == "file"
    ]
    voices = [
        message
        for message in outbound
        if str(message.get("msgType") or message.get("type") or "") == "voice"
    ]
    candidates = files or voices
    return (
        min(candidates, key=lambda item: int(item.get("msgTime") or 0))
        if candidates
        else None
    )


def format_time(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def compact_reply(message: dict[str, Any]) -> str:
    content = str(message.get("content") or "").strip()
    if not content:
        content = f"[{message.get('msgType') or message.get('type') or '消息'}]"
    return f"{format_time(int(message.get('msgTime') or 0))} {content}"


def classify(
    student: dict[str, Any],
    week: int,
    chat_dir: Path,
    cutoff_ms: int,
    fetch_error: str,
) -> tuple[list[str], bool]:
    prefix = [f"W{week}", "", student["id"], student["name"], student["class_time"], "否"]
    if student["leave"]:
        return [
            *prefix[:1],
            "请假",
            *prefix[2:],
            "是",
            "",
            "",
            student["reason"],
            "以学情表“请假”勾选为准",
        ], False

    chat_path = chat_dir / student["id"] / "latest.json"
    if fetch_error or not chat_path.exists():
        return [
            *prefix[:1],
            "未回复也未接龙",
            *prefix[2:],
            "否",
            "",
            "",
            student["reason"],
            "聊天记录读取失败，需人工复核",
        ], False

    messages = all_messages(chat_path)
    anchor = invitation_anchor(messages, cutoff_ms)
    if not anchor:
        return [
            *prefix[:1],
            "未回复也未接龙",
            *prefix[2:],
            "否",
            "",
            "",
            student["reason"],
            "未检索到本周邀约，需补触达",
        ], False

    anchor_time = int(anchor.get("msgTime") or 0)
    replies = [
        message
        for message in messages
        if int(message.get("flag", -1)) == 0
        and int(message.get("msgTime") or 0) > anchor_time
    ]
    category = "回复但是未接龙" if replies else "未回复也未接龙"
    return [
        *prefix[:1],
        category,
        *prefix[2:],
        "否",
        format_time(anchor_time),
        " | ".join(compact_reply(message) for message in replies),
        student["reason"],
        "家长已回复，尚未接龙" if replies else "邀约后未回复",
    ], True


def ensure_sheet(name: str) -> str:
    result = mcp_call("get_all_sheets", {"nodeId": NODE_ID})
    for sheet in result.get("sheets") or []:
        if sheet.get("name") == name:
            return str(sheet.get("sheetId") or sheet.get("id"))
    created = mcp_call("create_sheet", {"nodeId": NODE_ID, "name": name})
    sheet_id = created.get("sheetId") or created.get("id")
    if not sheet_id:
        raise RuntimeError(f"无法创建工作表 {name}：{created}")
    return str(sheet_id)


def existing_rows(sheet_id: str) -> list[list[str]]:
    result = mcp_call(
        "get_range",
        {"nodeId": NODE_ID, "sheetId": sheet_id, "range": "A1:K2000"},
    )
    if not result.get("success"):
        raise RuntimeError(f"无法读取现有邀约跟进表：{result}")
    values = result.get("values") or result.get("displayValues") or []
    if not values:
        return []
    headers = [str(value).strip() for value in values[0]]
    if not any(headers):
        return []
    if headers[: len(HEADERS)] != HEADERS:
        raise RuntimeError(
            f"邀约跟进表表头不一致，已停止覆盖：期望 {HEADERS}，实际 {headers}"
        )
    rows: list[list[str]] = []
    for row in values[1:]:
        padded = [str(value) for value in row[: len(HEADERS)]]
        padded += [""] * (len(HEADERS) - len(padded))
        if padded[2].strip():
            rows.append(padded)
    return rows


def merged_rows(
    old_rows: list[list[str]],
    new_rows: list[list[str]],
    week: int,
    class_prefix: str,
) -> tuple[list[list[str]], int]:
    week_label = f"W{week}"
    preserved = [
        row
        for row in old_rows
        if not (
            row[0].strip().upper() == week_label.upper()
            and row[4].strip().startswith(class_prefix)
        )
    ]
    replaced = len(old_rows) - len(preserved)
    combined = [*preserved, *new_rows]
    category_order = {"请假": 0, "回复但是未接龙": 1, "未回复也未接龙": 2}

    def week_number(label: str) -> int:
        value = label.strip().upper()
        return int(value[1:]) if value.startswith("W") and value[1:].isdigit() else 9999

    combined.sort(
        key=lambda row: (
            week_number(row[0]),
            row[4],
            category_order.get(row[1], 9),
            row[3],
        )
    )
    return combined, replaced


def write_sheet(sheet_id: str, rows: list[list[str]]) -> None:
    cleared = mcp_call(
        "clear_range",
        {"nodeId": NODE_ID, "sheetId": sheet_id, "range": "A:K"},
    )
    if not cleared.get("success"):
        raise RuntimeError(f"无法清空原跟进表：{cleared}")
    stream = StringIO()
    csv.writer(stream, lineterminator="\n").writerows([HEADERS, *rows])
    written = mcp_call(
        "set_range_from_csv",
        {
            "nodeId": NODE_ID,
            "sheetId": sheet_id,
            "startCell": "A1",
            "csv": stream.getvalue(),
            "allowOverwrite": True,
        },
    )
    if not written.get("success"):
        raise RuntimeError(f"无法写入跟进表：{written}")
    verified = mcp_call(
        "get_range",
        {
            "nodeId": NODE_ID,
            "sheetId": sheet_id,
            "range": f"A1:K{len(rows) + 1}",
        },
    )
    values = verified.get("values") or verified.get("displayValues") or []
    nonempty = [row for row in values[1:] if row and str(row[2]).strip()]
    if not verified.get("success") or len(nonempty) != len(rows):
        raise RuntimeError(
            f"跟进表回读不一致：期望 {len(rows)} 行，实际 {len(nonempty)} 行"
        )


def main() -> int:
    args = parse_args()
    context = context_for(week=args.week)
    china = timezone(timedelta(hours=8))
    cutoff = datetime.combine(context.start, time(0, 0), china)
    cutoff_ms = int(cutoff.timestamp() * 1000)

    students = learning_students(args.week, args.class_prefix)
    targets = [student for student in students if not student["chained"]]
    leaves = [student for student in targets if student["leave"]]
    chat_targets = [student for student in targets if not student["leave"]]
    chat_dir = (
        WORKSPACE
        / "data"
        / f"parent-chats-{PREFIX}-week{args.week}-{args.class_prefix}-invite"
    )
    chat_dir.mkdir(parents=True, exist_ok=True)

    errors: dict[str, str] = {}
    if not args.skip_fetch and not args.check_only:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(fetch_chat, student, args.port, chat_dir): student
                for student in chat_targets
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                user_id, error = future.result()
                if error:
                    errors[user_id] = error
                if completed % 10 == 0 or completed == len(futures):
                    print(
                        f"chat_fetch={completed}/{len(futures)} errors={len(errors)}",
                        flush=True,
                    )

    if args.check_only:
        print(
            json.dumps(
                {
                    "checkOnly": True,
                    "week": args.week,
                    "classPrefix": args.class_prefix,
                    "students": len(students),
                    "chained": len(students) - len(targets),
                    "followup": len(targets),
                    "leaveByLearningSheet": len(leaves),
                    "chatTargets": len(chat_targets),
                    "cutoff": cutoff.isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    rows: list[list[str]] = []
    invite_anchors = 0
    for student in targets:
        row, has_anchor = classify(
            student,
            args.week,
            chat_dir,
            cutoff_ms,
            errors.get(student["id"], ""),
        )
        rows.append(row)
        invite_anchors += int(has_anchor)

    category_order = {"请假": 0, "回复但是未接龙": 1, "未回复也未接龙": 2}
    rows.sort(key=lambda row: (category_order.get(row[1], 9), row[4], row[3]))
    output = (
        WORKSPACE
        / "data"
        / f"{PREFIX}-week{args.week}-{args.class_prefix}-invite-followup.csv"
    )
    with output.open("w", encoding="utf-8-sig", newline="") as file:
        csv.writer(file).writerows([HEADERS, *rows])

    sheet_id = ensure_sheet(args.sheet_name)
    old_rows = existing_rows(sheet_id)
    all_rows, replaced_rows = merged_rows(
        old_rows,
        rows,
        args.week,
        args.class_prefix,
    )
    write_sheet(sheet_id, all_rows)
    counts = {
        category: sum(row[1] == category for row in rows)
        for category in category_order
    }
    summary = {
        "week": args.week,
        "classPrefix": args.class_prefix,
        "students": len(students),
        "chained": len(students) - len(targets),
        "followupRows": len(rows),
        "leaveByLearningSheet": len(leaves),
        "inviteAnchors": invite_anchors,
        "counts": counts,
        "crmErrors": errors,
        "sheetId": sheet_id,
        "sheetName": args.sheet_name,
        "replacedRows": replaced_rows,
        "preservedRows": len(old_rows) - replaced_rows,
        "totalSheetRows": len(all_rows),
        "csv": str(output.relative_to(WORKSPACE)),
        "verifiedRows": len(rows),
    }
    summary_path = output.with_suffix(".summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
