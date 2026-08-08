#!/usr/bin/env python3
"""Build a daily service todo table from learning, segmentation, and renewal data."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from incremental_dingtalk_sheet import sync_rows_by_key

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


WORKSPACE = Path(__file__).resolve().parents[1]
DATA_DIR = WORKSPACE / "data"
OUTPUT_CSV = DATA_DIR / "daily-service-todo.csv"
COURSE_DATA_SKILL = WORKSPACE / "skills" / "codemao-course-data"
CLASS_CONFIG_PATH = DATA_DIR / "codemao-class-configs.json"
TODO_SHEET_NAME = "今日服务待办"


HEADERS = [
    "更新时间",
    "班级编号",
    "学员ID",
    "学生姓名",
    "来源",
    "优先级",
    "待办类型",
    "触发原因",
    "建议动作",
    "推荐话术/备注",
    "处理状态",
]


def read_rows(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.reader(file))


def write_rows(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.tmp{path.suffix}")
    with tmp.open("w", encoding="utf-8-sig", newline="") as file:
        csv.writer(file).writerows(rows)
    tmp.replace(path)


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def mcp_credentials() -> tuple[str, str]:
    sync = (COURSE_DATA_SKILL / "sync.py").read_text(encoding="utf-8", errors="ignore")
    url_match = re.search(r'MCP_URL\s*=\s*"([^"]+)"', sync)
    token_match = re.search(r'ACCESS_TOKEN\s*=\s*"([^"]+)"', sync)
    if not url_match or not token_match:
        raise RuntimeError("Cannot find MCP_URL or ACCESS_TOKEN in codemao-course-data/sync.py")
    return url_match.group(1), token_match.group(1)


def mcp_call(name: str, arguments: dict[str, object]) -> dict[str, object]:
    url, token = mcp_credentials()
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
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(1, 4):
        try:
            request = Request(url, data=body, headers=headers, method="POST")
            with urlopen(request, timeout=90) as response:
                result = json.loads(response.read().decode("utf-8"))
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
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as error:
            if attempt == 3:
                raise
            print(f"Retry MCP call {name} after error: {error}")
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
    if value.isascii() and value.isdigit() and len(value) > 1 and value.startswith("0"):
        # DingTalk otherwise displays class codes such as 0724 as the number 724.
        return f'="{value}"'
    if all(ord(ch) < 128 for ch in value):
        return value
    parts: list[str] = []
    ascii_buf: list[str] = []

    def flush_ascii() -> None:
        if ascii_buf:
            text = "".join(ascii_buf).replace('"', '""')
            parts.append(f'"{text}"')
            ascii_buf.clear()

    for ch in value:
        if ord(ch) < 128:
            ascii_buf.append(ch)
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
        for item in sheets
        if isinstance(item, dict)
    )
    if not exists:
        created = mcp_call("create_sheet", {"nodeId": node_id, "name": sheet_name})
        print(f"Created DingTalk sheet {sheet_name}: {json.dumps(created, ensure_ascii=False)[:300]}")


def write_dingtalk(rows: list[list[str]], sheet_name: str = TODO_SHEET_NAME, class_code: str = "0724") -> None:
    if class_code == "0724":
        config = read_json(CLASS_CONFIG_PATH)
        if not isinstance(config, dict):
            raise RuntimeError("Invalid codemao-class-configs.json")
        node_id = str(config["classes"]["0724"]["dingtalk"]["student_sheet_url"])
    else:
        config = read_json(COURSE_DATA_SKILL / "config.json")
        if not isinstance(config, dict):
            raise RuntimeError("Invalid course-data config.json")
        node_id = str(config["dingtalk"]["node_id"])
    if not isinstance(config, dict):
        raise RuntimeError("Invalid DingTalk config")
    ensure_sheet(node_id, sheet_name)
    stats = sync_rows_by_key(
        call=mcp_call,
        node_id=node_id,
        sheet_id=sheet_name,
        rows=rows,
        key_indexes=(2, 6),
        formula=formula_text,
        ignore_indexes=(0,),
        audit_file=DATA_DIR / "0724-incremental-change-log.jsonl",
    )
    print(f"Incrementally synced DingTalk sheet {sheet_name}: {len(rows) - 1} todo row(s), {stats}")


def cell(row: list[str], index: int) -> str:
    return str(row[index]).strip() if index < len(row) else ""


def add_todo(
    rows: list[list[str]],
    *,
    now: str,
    class_code: str,
    student_id: str,
    student_name: str,
    source: str,
    priority: str,
    todo_type: str,
    reason: str,
    action: str,
    note: str,
) -> None:
    rows.append(
        [
            now,
            class_code,
            student_id,
            student_name,
            source,
            priority,
            todo_type,
            reason,
            action,
            note,
            "未处理",
        ]
    )


def build_0724_todos(now: str, rows: list[list[str]]) -> None:
    """Use the new-class learning sheet export.

    Column layout is controlled by scripts/update_0724_learning_sheet.py:
    0 ID, 1 name, 5 class time, 6 class, 7 divide type,
    8 questionnaire, 9 wechat, 10 owner.
    """

    path = DATA_DIR / "new-class-student-questionnaire-selected-columns.csv"
    data = read_rows(path)
    for row in data[1:]:
        student_id = cell(row, 0)
        student_name = cell(row, 1)
        class_time = cell(row, 5)
        class_name = cell(row, 6)
        divide_type = cell(row, 7)
        questionnaire = cell(row, 8)
        wechat = cell(row, 9)
        owner = cell(row, 10)
        context = f"{class_name} {class_time} {divide_type}".strip()

        if questionnaire != "是":
            add_todo(
                rows,
                now=now,
                class_code="0724",
                student_id=student_id,
                student_name=student_name,
                source="0724学情表",
                priority="P1",
                todo_type="开课前问卷跟进",
                reason=f"是否填写问卷={questionnaire or '空'}；{context}",
                action="提醒家长补充开课前问卷，便于首课前了解孩子基础和学习目标。",
                note=f"订单归属人：{owner}",
            )

        if wechat != "是":
            add_todo(
                rows,
                now=now,
                class_code="0724",
                student_id=student_id,
                student_name=student_name,
                source="0724学情表",
                priority="P1",
                todo_type="企微添加跟进",
                reason=f"是否已加企微={wechat or '空'}；{context}",
                action="优先完成企微添加，否则后续课前提醒、课后反馈和续费铺垫会断链。",
                note=f"订单归属人：{owner}",
            )


def priority_from_risk(risk: str, opportunity: str) -> str:
    if risk == "高风险":
        return "P0"
    if risk in {"中高风险", "中风险"}:
        return "P1"
    if opportunity == "高机会":
        return "P1"
    return "P2"


def build_0109_renewal_todos(now: str, rows: list[list[str]]) -> None:
    """Use renewal-profile.csv.

    Current column layout from scripts/build_renewal_profiles.py:
    1 ID, 2 name, 3 class, 5 long-term layer, 6 learning score,
    7 learning risk tags, 17 opportunity, 18 risk, 19 final profile,
    20 high-risk method, 21 next action, 22 talk direction.
    """

    path = DATA_DIR / "renewal-profile.csv"
    data = read_rows(path)
    for row in data[1:]:
        student_id = cell(row, 1)
        student_name = cell(row, 2)
        class_name = cell(row, 3)
        layer = cell(row, 5)
        score = cell(row, 6)
        learning_tags = cell(row, 7)
        opportunity = cell(row, 17)
        risk = cell(row, 18)
        final_profile = cell(row, 19)
        high_risk_method = cell(row, 20)
        next_action = cell(row, 21)
        talk_track = cell(row, 22)

        if risk in {"高风险", "中高风险"}:
            add_todo(
                rows,
                now=now,
                class_code="0109",
                student_id=student_id,
                student_name=student_name,
                source="续费判断画像",
                priority=priority_from_risk(risk, opportunity),
                todo_type="续费风险私聊",
                reason=f"{risk}；{layer}；学习分={score}；{learning_tags}",
                action=next_action or "本周安排一次家长私聊，先处理阻力再谈续费。",
                note=high_risk_method or final_profile,
            )
            continue

        if opportunity == "高机会":
            add_todo(
                rows,
                now=now,
                class_code="0109",
                student_id=student_id,
                student_name=student_name,
                source="续费判断画像",
                priority="P1",
                todo_type="续费机会推进",
                reason=f"{opportunity}；{layer}；学习分={score}",
                action=next_action or "补充阶段成果和下一阶段目标，推进续费确认。",
                note=talk_track or final_profile,
            )
            continue

        if layer.startswith(("C-", "D-")):
            add_todo(
                rows,
                now=now,
                class_code="0109",
                student_id=student_id,
                student_name=student_name,
                source="长期学生分层",
                priority="P2",
                todo_type="学习状态修复",
                reason=f"{layer}；学习分={score}；{learning_tags}",
                action="先修复完课和题目表现，再进入续费沟通。",
                note=final_profile,
            )


def build_todos(class_codes: set[str]) -> list[list[str]]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = [HEADERS]
    if "0724" in class_codes:
        build_0724_todos(now, rows)
    if "0109" in class_codes:
        build_0109_renewal_todos(now, rows)
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    body = rows[1:]
    body.sort(key=lambda row: (priority_order.get(row[5], 9), row[1], row[2], row[6]))
    return [HEADERS, *body]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(OUTPUT_CSV))
    parser.add_argument("--write", action="store_true", help="Write the todo table to DingTalk")
    parser.add_argument("--sheet-name", default=TODO_SHEET_NAME)
    parser.add_argument(
        "--class-code",
        action="append",
        choices=["0724", "0109"],
        help="Class code to include. Defaults to 0724 only. Repeat to include more than one.",
    )
    args = parser.parse_args()
    class_codes = set(args.class_code or ["0724"])
    rows = build_todos(class_codes)
    write_rows(Path(args.output), rows)
    counts: dict[str, int] = {}
    for row in rows[1:]:
        counts[row[5]] = counts.get(row[5], 0) + 1
    print(f"Wrote {args.output}: {len(rows) - 1} todo row(s)")
    print("class_codes", sorted(class_codes))
    print("priority_counts", counts)
    if args.write:
        write_target = "0724" if class_codes == {"0724"} else "0109"
        write_dingtalk(rows, args.sheet_name, write_target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
