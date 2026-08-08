#!/usr/bin/env python3
"""Create one CRM enterprise-WeChat pending task per personalized feedback row."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from teacher_workbench_config import (
    class_mappings,
    data_path,
    data_prefix,
    load_workbench_config,
    script_config,
    wecom_config,
)

WORKSPACE = Path(__file__).resolve().parents[1]
CONFIG_PROFILE = script_config()
PREFIX = data_prefix(CONFIG_PROFILE)
INPUT_CSV = WORKSPACE / "data" / f"{PREFIX}-week1-pending-personalized-feedback.csv"
CONFIG_PATH = WORKSPACE / "data" / "new-class-group-send-cancel-config.json"
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
COOKIE_PATH = WORKSPACE / "data" / "crm-cookies.json"
COURSE_ID = 9336
UNLOCK_COURSE_IDS = [9336, 9335]


def load_crm_module():
    spec = importlib.util.spec_from_file_location("crm_personalized_send", CRM_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load CRM module: {CRM_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    required = {"学生ID", "学生姓名", "个性化反馈话术"}
    missing = required.difference(rows[0] if rows else {})
    if missing:
        raise RuntimeError(f"Missing columns: {sorted(missing)}")
    return rows


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
            "slot": row.get("class_name") or row.get("className") or row.get("term_name") or str(class_id),
            "name": row.get("class_name") or row.get("className") or str(class_id),
            "term_id": term_id,
            "class_id": class_id,
        }
    if not lookup:
        raise RuntimeError(f"No valid class_id/term_id rows in {path}")
    return lookup


def class_by_time_lookup() -> dict[str, int]:
    lookup: dict[str, int] = {}
    for class_id, label in class_mappings(CONFIG_PROFILE):
        clean_label = "".join(str(label or "").split())
        if clean_label:
            lookup[clean_label] = int(class_id)
    for row in CONFIG_PROFILE.get("classes") or []:
        if not isinstance(row, dict):
            continue
        try:
            class_id = int(row.get("class_id") or 0)
        except (TypeError, ValueError):
            continue
        for key in ("label", "match_prefix"):
            clean = "".join(str(row.get(key) or "").split())
            if class_id and clean:
                lookup[clean] = class_id
    return lookup


def class_id_from_row(row: dict[str, str], lookup: dict[str, int]) -> int | None:
    class_time = "".join(str(row.get("上课时间") or "").split())
    if not class_time:
        return None
    for label, class_id in lookup.items():
        if class_time.startswith(label) or label.startswith(class_time):
            return class_id
    return None


def persist_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def configured_path(pattern_or_path: str, *, week: int) -> Path:
    raw = str(pattern_or_path or "").format(prefix=PREFIX, week=week)
    path = Path(raw)
    return path if path.is_absolute() else WORKSPACE / path


def default_result_path(week: int) -> Path:
    wecom = wecom_config(CONFIG_PROFILE)
    pattern = str(wecom.get("send_result_pattern") or f"data/{PREFIX}-week{{week}}-feedback-send-result.json")
    return configured_path(pattern, week=week)


def chat_cache_path(week: int) -> Path:
    wecom = wecom_config(CONFIG_PROFILE)
    raw = str(wecom.get("chat_id_cache") or f"data/{PREFIX}-wecom-parent-chat-ids.json")
    return configured_path(raw, week=week)


def persist_chat_cache(path: Path, results: list[dict[str, Any]]) -> None:
    cache: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cache = loaded
        except (OSError, json.JSONDecodeError):
            cache = {}
    now = datetime.now().isoformat(timespec="seconds")
    for item in results:
        student_id = str(item.get("student_id") or "").strip()
        if not student_id:
            continue
        cache[student_id] = {
            "studentName": item.get("student_name") or "",
            "classId": item.get("class_id"),
            "sendable": bool(item.get("sendable")),
            "externalUserCount": item.get("external_user_count", 0),
            "parentUserCount": item.get("parent_user_count", 0),
            "source": "crm",
            "updatedAt": now,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def console_summary(output: dict[str, Any]) -> dict[str, Any]:
    results = output.get("results") or []
    blocked = [
        {
            "student_id": item.get("student_id"),
            "student_name": item.get("student_name"),
            "class_id": item.get("class_id"),
            "reason": item.get("reason"),
        }
        for item in results
        if not item.get("sendable")
    ]
    return {
        key: value
        for key, value in output.items()
        if key != "results"
    } | {
        "skipped_unsendable": len(blocked),
        "blocked": blocked,
    }


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT_CSV)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--week", type=int, default=1)
    parser.add_argument("--course-id", type=int, default=COURSE_ID)
    parser.add_argument(
        "--unlock-course-ids",
        type=int,
        nargs="+",
        default=UNLOCK_COURSE_IDS,
    )
    parser.add_argument(
        "--result",
        type=Path,
        default=None,
    )
    args = parser.parse_args()
    result_path = args.result or default_result_path(args.week)
    wecom = wecom_config(CONFIG_PROFILE)
    if args.execute and not bool(wecom.get("enabled")):
        raise RuntimeError(
            "企微反馈发送未在配置中开启。请先在 data/teacher-workbench-config.json 的 "
            "profile.wecom.enabled 改为 true，再重新执行发送。"
        )

    rows = load_rows(args.input)
    if not rows:
        raise RuntimeError("No feedback rows to send")

    crm = load_crm_module()
    config = crm.read_json(CONFIG_PATH)
    config["cookies_file"] = str(refresh_crm_cookies())
    profile_crm = CONFIG_PROFILE.get("crm") if isinstance(CONFIG_PROFILE.get("crm"), dict) else {}
    if int(profile_crm.get("class_pool_id") or 0) > 0:
        config["class_pool_id"] = int(profile_crm["class_pool_id"])
    config["classes"] = [
        {
            "name": item["slot"],
            "term_id": item["term_id"],
            "class_id": item["class_id"],
        }
        for item in real_class_lookup().values()
    ]
    config["defaults"]["tab_type"] = "1"
    config["defaults"]["has_study"] = True
    config["defaults"]["exclude_task_object_list"] = [
        {"code": 232, "name": "已请假", "type": 0}
    ]

    client = crm.CrmClient(config)
    class_by_id = crm.class_lookup(config)
    user_ids = [int(row["学生ID"]) for row in rows]
    grouped = client.classify_users(user_ids)
    class_by_user: dict[int, int] = {}
    if isinstance(grouped, list):
        for group in grouped:
            class_id = int(group["classId"])
            for user_id in group.get("userIds") or []:
                class_by_user[int(user_id)] = class_id
    class_time_lookup = class_by_time_lookup()
    for row in rows:
        user_id = int(row["学生ID"])
        if user_id not in class_by_user:
            fallback_class_id = class_id_from_row(row, class_time_lookup)
            if fallback_class_id:
                class_by_user[user_id] = fallback_class_id

    users_by_class: dict[int, list[int]] = {}
    for row in rows:
        user_id = int(row["学生ID"])
        class_id = class_by_user.get(user_id)
        if class_id:
            users_by_class.setdefault(class_id, []).append(user_id)
    send_context_by_user: dict[int, dict[str, Any]] = {}
    for class_id, ids in users_by_class.items():
        class_item = class_by_id.get(class_id)
        if class_item is None:
            continue
        unique_ids = list(dict.fromkeys(ids))
        checked_ids = client.checked_user_ids(
            class_id,
            int(class_item["term_id"]),
            unique_ids,
            args.unlock_course_ids,
        )
        wx_users = client.user_wechat_info(class_id, checked_ids)
        wx_by_user: dict[int, list[dict[str, Any]]] = {}
        for user in wx_users:
            if not isinstance(user, dict) or not user.get("userId"):
                continue
            wx_by_user.setdefault(int(user["userId"]), []).append(user)
        checked_set = {int(value) for value in checked_ids}
        for user_id in unique_ids:
            send_context_by_user[user_id] = {
                "checked_ids": [user_id] if user_id in checked_set else [],
                "wx_users": wx_by_user.get(user_id, []),
            }

    results: list[dict[str, Any]] = []
    send_payloads: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        user_id = int(row["学生ID"])
        class_id = class_by_user.get(user_id)
        class_item = class_by_id.get(class_id or 0)
        item: dict[str, Any] = {
            "student_id": str(user_id),
            "student_name": row["学生姓名"],
            "class_id": class_id,
            "sendable": False,
            "created": False,
            "message_sha256": hashlib.sha256(
                row["个性化反馈话术"].encode("utf-8")
            ).hexdigest(),
        }
        if class_item is None:
            item["reason"] = "no_class_mapping"
            results.append(item)
            continue

        send_context = send_context_by_user.get(user_id, {})
        checked_ids = list(send_context.get("checked_ids") or [])
        wx_users = list(send_context.get("wx_users") or [])
        payload = crm.notify_payload(
            config,
            class_item,
            wx_users,
            checked_ids,
            row["个性化反馈话术"],
            args.course_id,
        )
        parent_user_count = len(wx_users)
        external_user_count = sum(
            len(user.get("externalUserIds") or [])
            for user in wx_users
            if isinstance(user, dict)
        )
        item.update(
            {
                "parent_user_count": parent_user_count,
                "external_user_count": external_user_count,
                "notify_user_count": len(payload["users"]),
            }
        )
        if len(payload["users"]) != 1:
            item["reason"] = f"expected_one_sendable_parent_got_{len(payload['users'])}"
            results.append(item)
            continue
        item["sendable"] = True
        results.append(item)
        send_payloads.append((user_id, item, payload))

    blocked = [item for item in results if not item.get("sendable")]
    for item in blocked:
        item["skipped"] = True
        item["skip_reason"] = item.get("reason") or "unsendable_wecom_mapping"

    output = {
        "mode": "execute" if args.execute else "dry-run",
        "week": args.week,
        "course_id": args.course_id,
        "targets": len(rows),
        "sendable": sum(item["sendable"] for item in results),
        "skipped_unsendable": sum(not item["sendable"] for item in results),
        "created": sum(item["created"] for item in results),
        "result_path": str(result_path),
        "chat_id_cache": str(chat_cache_path(args.week)),
        "results": results,
    }
    if not args.execute:
        persist_chat_cache(chat_cache_path(args.week), results)
        persist_result(
            result_path,
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                **output,
            },
        )
        print(json.dumps(console_summary(output), ensure_ascii=False, indent=2))
        print("Dry run only; no CRM tasks were created.")
        return 0

    if not send_payloads:
        persist_chat_cache(chat_cache_path(args.week), results)
        persist_result(
            result_path,
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                **output,
            },
        )
        raise RuntimeError(f"没有可发送的企微映射，未创建任务；请查看 {result_path}")

    for user_id, item, payload in send_payloads:
        response = client.send_notify(payload)
        if response.get("success") is not True and response.get("code") != 200:
            item["response"] = response
            persist_result(
                result_path,
                {
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "mode": "execute",
                    "week": args.week,
                    "course_id": args.course_id,
                    "results": results,
                },
            )
            raise RuntimeError(f"CRM notify failed for user {user_id}")
        item["created"] = True
        item["response_message"] = response.get("msg", "OK")
        persist_result(
            result_path,
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "mode": "execute",
                "week": args.week,
                "course_id": args.course_id,
                "results": results,
            },
        )

    output = {
        "mode": "execute",
        "week": args.week,
        "course_id": args.course_id,
        "targets": len(rows),
        "sendable": sum(item["sendable"] for item in results),
        "skipped_unsendable": sum(not item["sendable"] for item in results),
        "created": sum(item["created"] for item in results),
        "result_path": str(result_path),
        "chat_id_cache": str(chat_cache_path(args.week)),
        "results": results,
    }
    persist_chat_cache(chat_cache_path(args.week), results)
    persist_result(
        result_path,
        {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            **output,
        }
    )
    print(json.dumps(console_summary(output), ensure_ascii=False, indent=2))
    if blocked:
        print(f"Skipped {len(blocked)} unsendable student(s); sendable CRM pending tasks were still created.")
    print("CRM pending tasks created; final sending still requires enterprise WeChat confirmation.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
