#!/usr/bin/env python3
"""Detect parents who did not reply to the teacher's latest WeCom message.

Data source: local parent-chat captures under <workspace>/data/parent-chats-*.
Each student capture has conversations[].messages[]. A message is from the
parent when its messageId ends with '_external'; otherwise it is from the
teacher/staff. A parent is flagged as unreplied when the teacher sent the most
recent message in that student's timeline (i.e. the parent did not respond
after the teacher's last message).

Outputs a JSON/CSV of unreplied parents.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WORKSPACE = Path(__file__).resolve().parents[1]
DATA = WORKSPACE / "data"


def is_parent_message(message: dict) -> bool:
    """flag == 0 means the message came from the parent (external contact)."""
    return message.get("flag") == 0


def is_teacher_message(message: dict) -> bool:
    """flag == 1 means the message came from the teacher/staff."""
    return message.get("flag") == 1


def msg_time(message: dict):
    try:
        return int(message.get("msgTime") or 0)
    except (TypeError, ValueError):
        return 0


# 通话/语音记录不计为“家长最后发的消息”，不作为未回复依据
CALL_TYPES = {"voiptext", "call", "voip", "audio_call", "video_call"}


def is_call_message(message: dict) -> bool:
    msg_type = str(message.get("msgType") or message.get("type") or "").lower()
    return msg_type in CALL_TYPES


# 非文本消息类型的展示标签（content 为空时）
NON_TEXT_LABELS = {
    "image": "图片",
    "voice": "语音",
    "emoji": "图片",
    "emotion": "图片",
    "video": "视频",
    "file": "文件",
    "link": "链接",
    "location": "位置",
}


def message_display(message: dict) -> str:
    """Text to show for a message: content text if any, else a label by type."""
    content = str(message.get("content") or "").strip()
    if content:
        return content
    msg_type = str(message.get("msgType") or message.get("type") or "").lower()
    return NON_TEXT_LABELS.get(msg_type, "")


def analyze_student(latest: dict) -> dict | None:
    """Return unreplied info for a student, or None if parent replied / no teacher msg."""
    convs = latest.get("conversations") or []
    all_messages: list[dict] = []
    wechat_user = None
    teacher_name = ""
    for conv in convs:
        wu = conv.get("wechatUser")
        if wu and wechat_user is None:
            wechat_user = wu
        messages = conv.get("messages") or []
        for m in messages:
            all_messages.append(m)
            if m.get("teacherNickName"):
                teacher_name = m["teacherNickName"]
    # 先剔除通话/语音记录，避免把“通话”当成家长未回复的依据
    all_messages = [m for m in all_messages if not is_call_message(m)]
    if not all_messages:
        return None
    all_messages.sort(key=msg_time)
    last = all_messages[-1]
    # 未回复 = 家长发了消息（flag=0）且是最后一条，老师（flag=1）还没回复
    if not is_parent_message(last):
        return None  # 老师最后回复了 -> 已回复
    parent_msgs = [m for m in all_messages if is_parent_message(m)]
    teacher_msgs = [m for m in all_messages if is_teacher_message(m)]
    parent_name = ""
    if wechat_user:
        parent_name = str(wechat_user.get("wechatRemark") or wechat_user.get("wechatName") or "")
    parent_last = parent_msgs[-1] if parent_msgs else {}
    teacher_last = teacher_msgs[-1] if teacher_msgs else {}
    return {
        "student_id": str(latest.get("userId") or "").strip(),
        "parent_wechat": parent_name,
        "teacher": teacher_name or "",
        "parent_last_msg_at": datetime.fromtimestamp(
            msg_time(last) / 1000
        ).strftime("%Y-%m-%d %H:%M") if msg_time(last) else "",
        "teacher_last_msg_at": datetime.fromtimestamp(
            msg_time(teacher_last) / 1000
        ).strftime("%Y-%m-%d %H:%M") if teacher_last and msg_time(teacher_last) else "",
        "parent_last_msg": message_display(parent_last),
        "teacher_last_msg": message_display(teacher_last),
        "total_msgs": len(all_messages),
        "parent_msgs": len(parent_msgs),
        "teacher_msgs": len(teacher_msgs),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dirs", default="", help="逗号分隔的目录名；默认所有 parent-chats*（按学生取最新触达）")
    parser.add_argument("--latest-only", action="store_true", help="仅统计每个学生最新一次触达")
    parser.add_argument("--class-code", default="", help="班级代号；指定则只保留该班学生（读 data/new-class-student-list.json）")
    parser.add_argument("--since-days", type=int, default=0, help="只保留家长最后发言在最近 N 天内的（0=不限）")
    parser.add_argument("--out", type=Path, default=DATA / "unreplied-parents.json")
    parser.add_argument("--csv", type=Path, default=DATA / "unreplied-parents.csv")
    args = parser.parse_args()

    dirs = [d for d in glob.glob(str(DATA / "parent-chats*")) if os.path.isdir(d)]
    if args.dirs:
        wanted = {name.strip() for name in args.dirs.split(",") if name.strip()}
        dirs = [d for d in dirs if os.path.basename(d) in wanted]

    # student_id -> (fetched_at, analysis) keep the newest capture
    newest: dict[str, tuple[str, dict]] = {}
    for d in dirs:
        for student_dir in glob.glob(os.path.join(d, "*")):
            if not os.path.isdir(student_dir):
                continue
            latest_path = os.path.join(student_dir, "latest.json")
            if not os.path.exists(latest_path):
                continue
            try:
                latest = json.load(open(latest_path, encoding="utf-8"))
            except Exception:
                continue
            student_id = str(latest.get("userId") or os.path.basename(student_dir)).strip()
            if not student_id:
                continue
            fetched = str(latest.get("fetchedAt") or "")
            info = analyze_student(latest)
            if info is None:
                continue
            if student_id not in newest or fetched >= newest[student_id][0]:
                newest[student_id] = (fetched, info)

    rows = [info for _, info in newest.values()]
    # 名单过滤：无论是否指定 class-code，都只保留当前教学名单（new-class-student-list.json）内的学生。
    roster_path = DATA / "new-class-student-list.json"
    class_ids: set[str] = set()
    if roster_path.exists():
        roster = json.loads(roster_path.read_text(encoding="utf-8"))
        items = roster.get("data", {}).get("items") if isinstance(roster.get("data"), dict) else roster.get("items") or (roster if isinstance(roster, list) else [])
        for item in items:
            uid = item.get("userId")
            if uid is not None:
                class_ids.add(str(uid).strip())
    if class_ids:
        before = len(rows)
        rows = [r for r in rows if r["student_id"] in class_ids]
        if args.class_code:
            print(f"按班级 {args.class_code} 过滤后未回复: {len(rows)}（名单 {len(class_ids)} 人，原 {before}）", flush=True)

    # 时间过滤：只保留家长最后发言落在"最近 since_days 天内"（含今天，按日历日）
    if args.since_days > 0:
        today = datetime.now()
        cutoff_dt = (today - timedelta(days=args.since_days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M")
        before = len(rows)
        rows = [r for r in rows if r.get("parent_last_msg_at", "") >= cutoff]
        print(f"最近 {args.since_days} 天（含今天，{cutoff} 起）未回复: {len(rows)}（原 {before}）", flush=True)

    rows.sort(key=lambda r: r["parent_last_msg_at"], reverse=True)
    print(f"家长发消息但老师未回复的家长数: {len(rows)}", flush=True)

    for r in rows:
        print(
            f"  {r['parent_last_msg_at'] or '--'}  {r['teacher']}  {r['parent_wechat'][:28]:28}  学生{r['student_id']}  (总{r['total_msgs']}条/家长{r['parent_msgs']}条/老师{r['teacher_msgs']}条)",
            flush=True,
        )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"unreplied_count": len(rows), "students": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
            writer.writeheader()
            writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
