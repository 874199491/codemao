#!/usr/bin/env python3
"""Mark a week's solitaire checkbox from recent CRM private replies.

Only a parent's reply of exactly ``1``, an acknowledgement such as ``好的`` /
``收到`` / ``OK``, a post-reminder sticker, or an explicit attendance phrase,
sent after a teacher voice or class-reminder text from yesterday or today by
default, is eligible. CRM is read-only; DingTalk is updated only with
``--apply`` and is always verified by ``update_weekly_solitaire.py``.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from teacher_workbench_config import class_mappings, data_path, data_prefix, script_config

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPTS = WORKSPACE / "scripts"
try:
    TZ = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:  # Windows often has no tzdata package installed.
    TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", type=int, required=True, help="Target W week")
    parser.add_argument("--date", default=date.today().isoformat(), help="Window end date, YYYY-MM-DD")
    parser.add_argument("--lookback-days", type=int, default=2, help="Number of calendar days to inspect, including --date (default: 2)")
    parser.add_argument("--port", type=int, default=9223)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--class-time", action="append", default=[], help="Limit to configured class labels; repeatable")
    parser.add_argument("--reuse-captures", action="store_true", help="Re-evaluate already captured chats instead of fetching CRM again")
    parser.add_argument("--apply", action="store_true", help="Write matching students to DingTalk")
    return parser.parse_args()


def local_day(message: dict) -> date | None:
    try:
        return datetime.fromtimestamp(int(message.get("msgTime") or 0) / 1000, TZ).date()
    except (TypeError, ValueError, OSError):
        return None


def is_confirmed(text: object) -> bool:
    value = str(text or "").strip().replace("\u3000", " ")
    normalized = value.lower().replace(" ", "")
    return normalized in {"1", "ok", "[ok]", "👌", "👍", "[赞]", "[收到]"} or any(word in value for word in ("好的", "好哒", "收到", "了解", "参加", "能来", "会来", "来参加", "准时到", "可以到", "到课", "出席", "报名"))


def is_confirmation_message(message: dict) -> bool:
    """Treat any post-reminder sticker as an acknowledgement, plus known text confirmations."""
    message_type = str(message.get("msgType") or "").lower()
    return message_type == "emotion" or (message_type == "text" and is_confirmed(message.get("content")))


def is_reminder_anchor(message: dict) -> bool:
    """Accept teacher voice reminders and text messages that look like class reminders."""
    if message.get("flag") != 1:
        return False
    message_type = str(message.get("msgType") or "").lower()
    if message_type == "voice":
        return True
    if message_type != "text":
        return False
    content = str(message.get("content") or "").lower().replace(" ", "")
    return any(token in content for token in ("上课", "上课提醒", "课程提醒", "回复1", "回复：1", "回复:1", "按时参加", "准时参加", "接龙"))


def fetch_one(user_id: str, out_dir: Path, port: int, days: int) -> tuple[str, str]:
    command = ["node", str(SCRIPTS / "fetch_parent_chat_from_crm.mjs"), "--port", str(port), "--user-id", user_id, "--days", str(days), "--limit", "100", "--out-dir", str(out_dir)]
    result = subprocess.run(command, cwd=WORKSPACE, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", timeout=180)
    return user_id, "" if result.returncode == 0 else result.stdout[-500:]


def main() -> int:
    opt = args()
    target_day = date.fromisoformat(opt.date)
    if opt.lookback_days < 1:
        raise RuntimeError("--lookback-days 至少为 1。")
    target_days = {target_day - timedelta(days=offset) for offset in range(opt.lookback_days)}
    config = script_config()
    requested_labels = {value.strip() for value in opt.class_time if value.strip()}
    configured_classes = [(class_id, label) for class_id, label in class_mappings(config) if label.startswith(("周五", "周六"))]
    selected_classes = [(class_id, label) for class_id, label in configured_classes if not requested_labels or label in requested_labels]
    if not selected_classes:
        raise RuntimeError("当前工作台没有可用的周五/周六班级，未读取 CRM 或写入钉钉。")
    labels_by_class = {int(class_id): label for class_id, label in selected_classes}
    # The completion-detail snapshot is useful for lesson status but can be an
    # old export without ``realClassId``.  The fresh CRM roster is authoritative
    # for private-chat targets, so prefer it and only fall back when needed.
    roster_candidates = [
        WORKSPACE / "data" / "new-class-student-list.json",
        data_path("students_json", config),
    ]
    roster_path: Path | None = None
    students: list[dict] = []
    for candidate in roster_candidates:
        if not candidate.exists():
            continue
        roster_data = json.loads(candidate.read_text(encoding="utf-8"))
        items = roster_data.get("data", {}).get("items") or roster_data.get("items") or []
        matched = [row for row in items if int(row.get("realClassId") or 0) in labels_by_class]
        if matched:
            roster_path, students = candidate, matched
            break
    if not students:
        checked = "、".join(str(path) for path in roster_candidates)
        raise RuntimeError(f"名单中没有所选周五/周六班学员，已停止。已检查：{checked}")
    prefix = data_prefix(config)
    runtime = WORKSPACE / "data" / f"{prefix}-private-voice-solitaire-{target_day:%Y%m%d}"
    captures = runtime / "parent-chats"
    captures.mkdir(parents=True, exist_ok=True)
    if not opt.reuse_captures:
        failures: dict[str, str] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(opt.workers, 12))) as pool:
            futures = [pool.submit(fetch_one, str(row["userId"]), captures, opt.port, opt.lookback_days) for row in students]
            for future in concurrent.futures.as_completed(futures):
                user_id, error = future.result()
                if error:
                    failures[user_id] = error
        if failures:
            raise RuntimeError(f"CRM 私聊读取失败 {len(failures)} 人；为避免漏记，本次未写入钉钉。")
    matches, no_reminder = [], []
    for student in students:
        user_id = str(student["userId"])
        path = captures / user_id / "latest.json"
        if not path.exists():
            raise RuntimeError(f"缺少 CRM 私聊记录 {user_id}；本次未写入钉钉。")
        data = json.loads(path.read_text(encoding="utf-8"))
        messages = [message for convo in data.get("conversations") or [] for message in convo.get("messages") or [] if local_day(message) in target_days]
        reminders = [message for message in messages if is_reminder_anchor(message)]
        if not reminders:
            no_reminder.append(user_id)
            continue
        replies = [message for message in messages if message.get("flag") == 0 and is_confirmation_message(message) and any(int(message.get("msgTime") or 0) > int(reminder.get("msgTime") or 0) for reminder in reminders)]
        if replies:
            reply = min(replies, key=lambda message: int(message.get("msgTime") or 0))
            matches.append({"userId": user_id, "studentName": student.get("childName") or "", "classTime": labels_by_class[int(student["realClassId"])], "reply": str(reply.get("content") or "").strip(), "replyAt": datetime.fromtimestamp(int(reply["msgTime"]) / 1000, TZ).isoformat(timespec="seconds")})
    payload = {"source": "CRM私聊上课提醒", "date": target_day.isoformat(), "dates": [value.isoformat() for value in sorted(target_days)], "classTimes": [label for _, label in selected_classes], "groups": [{"source": "private_voice", "classId": class_id, "classTime": label} for class_id, label in selected_classes], "uniqueSolitaireSenderCount": len(matches), "matchedCount": len(matches), "reviewCount": 0, "rows": matches, "students": len(students), "withoutReminderAnchor": no_reminder}
    source = runtime / "latest.json"
    source.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    command = ["py", "-3.10", str(SCRIPTS / "update_weekly_solitaire.py"), "--week", str(opt.week), "--solitaire", str(source)]
    for _, label in selected_classes:
        command.extend(("--class-time", label))
    if not opt.apply:
        command.append("--check-only")
    result = subprocess.run(command, cwd=WORKSPACE, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise SystemExit(result.returncode)
    print(json.dumps({"privateVoiceReplies": len(matches), "source": str(source), "applied": opt.apply}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
