#!/usr/bin/env python3
"""Cancel monthly-exam WeCom pending sends and make local rows sendable again.

The script is intentionally scoped to the monthly-exam feedback runtime:
``data/monthly-exam-feedback``. It uses the configured teacher profile to find
the current three CRM classes, scans recent CRM group-send records, cancels only
the matched student message IDs, then clears local sent markers for the students
whose CRM messages were actually canceled.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPTS = WORKSPACE / "scripts"
sys.path.insert(0, str(SCRIPTS))

from teacher_workbench_config import data_path, script_config  # noqa: E402


RUNTIME = WORKSPACE / "data" / "monthly-exam-feedback"
COOKIE_PATH = WORKSPACE / "data" / "crm-cookies.json"


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cookie_header(cookies: Any) -> str:
    if isinstance(cookies, dict):
        return "; ".join(f"{key}={value}" for key, value in cookies.items())
    if isinstance(cookies, list):
        return "; ".join(
            f"{item['name']}={item['value']}"
            for item in cookies
            if isinstance(item, dict) and item.get("name") and item.get("value")
        )
    raise RuntimeError("Unsupported cookie format")


def chrome_debug_port() -> int:
    config_path = WORKSPACE / "data" / "teacher-workbench-config.json"
    try:
        payload = read_json(config_path)
        return int(payload.get("chrome_debug_port") or 9223)
    except Exception:
        return 9223


def refresh_crm_cookies() -> None:
    """Refresh CRM cookies when the packaged helper exists; otherwise use cache."""
    helper = WORKSPACE / "skills" / "codemao-makeup-reminder" / "scripts" / "export_crm_cookies_from_chrome.mjs"
    if helper.is_file():
        subprocess.run(
            ["node", str(helper), "--port", str(chrome_debug_port()), "--out", str(COOKIE_PATH)],
            cwd=WORKSPACE,
            check=True,
        )
    if not COOKIE_PATH.is_file():
        raise RuntimeError(f"未找到 CRM Cookie 缓存：{COOKIE_PATH}；请先在工作台打开/登录 CRM")


def configured_class_ids() -> set[int]:
    profile = script_config()
    ids: set[int] = set()
    for row in profile.get("classes") or []:
        if not isinstance(row, dict):
            continue
        try:
            class_id = int(row.get("class_id") or 0)
        except (TypeError, ValueError):
            continue
        if class_id:
            ids.add(class_id)
    if not ids:
        raise RuntimeError("当前配置没有可用 CRM 班级 class_id")
    return ids


def load_classes() -> list[dict[str, Any]]:
    profile = script_config()
    class_csv = data_path("completion_classes_csv", profile)
    expected_ids = configured_class_ids()
    rows: list[dict[str, Any]] = []
    if not class_csv.is_file():
        raise RuntimeError(f"未找到 CRM 班级缓存：{class_csv}；请先运行“生成配置”或“核对并更新学生时间段”")
    with class_csv.open("r", encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            try:
                class_id = int(row.get("class_id") or 0)
                term_id = int(row.get("term_id") or 0)
            except (TypeError, ValueError):
                continue
            if class_id in expected_ids and term_id > 0:
                rows.append(
                    {
                        "class_id": class_id,
                        "term_id": term_id,
                        "name": row.get("term_name") or row.get("class_name") or str(class_id),
                    }
                )
    if not rows:
        raise RuntimeError(f"班级缓存中没有匹配当前配置的班级：{class_csv}")
    return rows


def target_ids(max_score: float | None, created_after: str, student_ids: list[str]) -> list[str]:
    explicit = list(dict.fromkeys(str(value).strip() for value in student_ids if str(value).strip()))
    if explicit:
        return explicit
    status_path = RUNTIME / "sent-status.json"
    if not status_path.exists():
        raise RuntimeError(f"Cannot find sent status: {status_path}")
    status = read_json(status_path)
    ids: list[str] = []
    for student_id, item in status.items():
        if not isinstance(item, dict):
            continue
        try:
            score = float(item.get("score"))
        except (TypeError, ValueError):
            continue
        sent_at = str(item.get("sent_at") or "")
        if max_score is not None and score > max_score:
            continue
        if created_after and sent_at < created_after:
            continue
        ids.append(str(student_id).strip())
    return list(dict.fromkeys(value for value in ids if value))


def session():
    import requests

    refresh_crm_cookies()
    cookies = read_json(COOKIE_PATH)
    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Cookie": cookie_header(cookies),
            "Origin": "https://codecamp-crm.codemao.cn",
            "Referer": "https://codecamp-crm.codemao.cn/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "authorization_type": "3",
        }
    )
    if isinstance(cookies, dict) and cookies.get("admin-authorization"):
        sess.headers["admin-authorization"] = str(cookies["admin-authorization"])
    return sess


def post(sess, url: str, payload: dict[str, Any], *, timeout: int = 30) -> Any:
    response = sess.post(url, json=payload, timeout=timeout)
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:1000]}")
    data = response.json()
    if data.get("success") is not True and data.get("code") != 200:
        raise RuntimeError(json.dumps(data, ensure_ascii=False)[:1200])
    return data.get("data")


def record_pages(sess, class_item: dict[str, Any], page_size: int, max_pages: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page_index in range(1, max_pages + 1):
        print(f"扫描 {class_item['name']} 第 {page_index} 页...", flush=True)
        data = post(
            sess,
            "https://lbk-crm-teacher-web-api.codemao.cn/qwb/send/message/record/page",
            {
                "termId": int(class_item["term_id"]),
                "classId": int(class_item["class_id"]),
                "pageIndex": page_index,
                "pageSize": page_size,
            },
        )
        page_items = data.get("items") if isinstance(data, dict) else []
        if not page_items:
            break
        items.extend(page_items)
        if len(page_items) < page_size:
            break
    return items


def qwb_info(sess, class_item: dict[str, Any], record_id: int, user_ids: list[int]) -> list[dict[str, Any]]:
    data = post(
        sess,
        "https://cloud-gateway.codemao.cn/crm-rocket/ranking-list/ext/userQwbInfo",
        {
            "classId": int(class_item["class_id"]),
            "checkedUserIdList": user_ids,
            "termId": int(class_item["term_id"]),
            "channelType": 1,
            "sendType": 3,
            "recordId": record_id,
            "isShowAll": True,
        },
    )
    return data if isinstance(data, list) else []


def find_cancel_targets(sess, wanted_ids: list[str], page_size: int, max_pages: int) -> list[dict[str, Any]]:
    wanted = {int(value) for value in wanted_ids if str(value).isdigit()}
    targets: list[dict[str, Any]] = []
    for class_item in load_classes():
        for record in record_pages(sess, class_item, page_size, max_pages):
            record_id = int(record.get("id") or 0)
            choose_ids = [int(value) for value in record.get("chooseUserList") or [] if str(value).isdigit()]
            matched = [value for value in choose_ids if value in wanted]
            if not matched:
                continue
            details = qwb_info(sess, class_item, record_id, matched)
            msg_send_ids = [
                int(row["msgSendId"])
                for row in details
                if row.get("msgSendId") and int(row.get("userId") or 0) in matched
            ]
            if msg_send_ids:
                targets.append(
                    {
                        "className": class_item["name"],
                        "recordId": record_id,
                        "userIds": matched,
                        "msgSendIds": msg_send_ids,
                    }
                )
    return targets


def cancel_targets(sess, targets: list[dict[str, Any]]) -> tuple[int, int, int, list[str]]:
    canceled = failed = already_confirmed = 0
    canceled_student_ids: list[str] = []
    for target in targets:
        print(
            f"取消 {target['className']} recordId={target['recordId']} "
            f"userIds={target['userIds']} msgSendIds={target['msgSendIds']}",
            flush=True,
        )
        try:
            data = post(
                sess,
                "https://lbk-crm-teacher-web-api.codemao.cn/work-wechat/cancelByMsgIds",
                {
                    "msgSendIds": target["msgSendIds"],
                    "recordId": int(target["recordId"]),
                    "hasLabel": "true",
                },
            )
            print(f"  OK: {data}", flush=True)
            canceled += len(target["msgSendIds"])
            canceled_student_ids.extend(str(value) for value in target["userIds"])
        except Exception as error:
            message = str(error)
            if "客户端确认发送" in message or "发送状态已变更" in message:
                print("  跳过：企微客户端已确认发送，CRM 不允许取消", flush=True)
                already_confirmed += len(target["msgSendIds"])
            else:
                print(f"  失败：{message}", flush=True)
                failed += len(target["msgSendIds"])
    return canceled, failed, already_confirmed, list(dict.fromkeys(canceled_student_ids))


def mark_sendable(ids: list[str], reason: str) -> dict[str, Any]:
    status_path = RUNTIME / "sent-status.json"
    status = read_json(status_path) if status_path.exists() else {}
    removed_status = 0
    for student_id in ids:
        if str(student_id) in status:
            status.pop(str(student_id), None)
            removed_status += 1
    write_json(status_path, status)

    changed_results = 0
    for student_id in ids:
        result_path = RUNTIME / "results" / f"{student_id}.json"
        if not result_path.exists():
            continue
        result = read_json(result_path)
        if result.get("created") is True:
            result["created"] = False
            result["invalidated_by_cancel"] = True
            result["invalidated_at"] = datetime.now().isoformat(timespec="seconds")
            result["invalidated_reason"] = reason
            changed_results += 1
            write_json(result_path, result)
    return {"removed_sent_status": removed_status, "changed_result_files": changed_results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE, help="兼容工作台调用；脚本会以自身所在目录为准")
    parser.add_argument("--student-id", action="append", default=[], help="指定要取消的学生 ID，可重复传入")
    parser.add_argument("--max-score", type=float, default=None, help="未指定学生时，可按分数上限筛选已发送记录")
    parser.add_argument("--created-after", default="")
    parser.add_argument("--page-size", type=int, default=30)
    parser.add_argument("--max-pages", type=int, default=60, help="扫描页数上限；每班会扫到空页才停，足够覆盖全部记录")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    ids = target_ids(args.max_score, args.created_after, args.student_id)
    print(
        json.dumps(
            {
                "mode": "execute" if args.execute else "dry-run",
                "target_count": len(ids),
                "student_ids": ids,
                "max_score": args.max_score,
                "created_after": args.created_after,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    if not ids:
        return 0
    sess = session()
    targets = find_cancel_targets(sess, ids, args.page_size, args.max_pages)
    matched_ids = sorted({str(uid) for target in targets for uid in target["userIds"]}, key=int)
    missing_ids = sorted(set(ids) - set(matched_ids), key=lambda value: int(value) if value.isdigit() else value)
    print(
        json.dumps(
            {
                "cancel_record_count": len(targets),
                "matched_student_count": len(matched_ids),
                "missing_student_count": len(missing_ids),
                "missing_student_ids": missing_ids,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    if not args.execute:
        return 0

    canceled, failed, already_confirmed, canceled_student_ids = cancel_targets(sess, targets)
    local = mark_sendable(canceled_student_ids, "取消月考反馈后恢复可发送")
    summary = {
        "canceled_msg_count": canceled,
        "failed_msg_count": failed,
        "already_confirmed_msg_count": already_confirmed,
        "requested_student_count": len(ids),
        "matched_student_count": len(matched_ids),
        "marked_sendable_student_count": len(canceled_student_ids),
        "marked_sendable_student_ids": canceled_student_ids,
        "missing_student_ids": missing_ids,
        **local,
    }
    write_json(RUNTIME / "cancel-summary-latest.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
