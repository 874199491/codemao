#!/usr/bin/env python3
"""Cancel CodeMao CRM enterprise WeChat send records.

Default mode is a dry run. Pass --execute to call cancelByMsgIds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from create_makeup_reminder import CrmClient, DEFAULT_CONFIG, read_json

def read_ids(path: Path | None) -> list[int]:
    if not path:
        return []
    text = path.read_text(encoding="utf-8-sig")
    ids = [int(match) for match in re.findall(r"\d+", text)]
    seen: set[int] = set()
    result: list[int] = []
    for user_id in ids:
        if user_id not in seen:
            seen.add(user_id)
            result.append(user_id)
    return result


def expect_success(response: dict[str, Any]) -> Any:
    if response.get("success") is not True and response.get("code") != 200:
        raise SystemExit(f"CRM API failed: {json.dumps(response, ensure_ascii=False)[:2000]}")
    return response.get("data")


def record_page(client: CrmClient, term_id: int, class_id: int, page_size: int) -> list[dict[str, Any]]:
    """Read every page so older cancellable sends are not left behind."""
    items: list[dict[str, Any]] = []
    page_index = 1
    expected_total: int | None = None
    while page_index <= 100:
        data = expect_success(
            client.post(
                f"{client.lbk_base}/qwb/send/message/record/page",
                {"termId": term_id, "classId": class_id, "pageIndex": page_index, "pageSize": page_size},
            )
        )
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise SystemExit(f"Unexpected record page response: {data!r}")
        page_items = data["items"]
        items.extend(page_items)
        for key in ("total", "totalCount", "count"):
            if isinstance(data.get(key), int):
                expected_total = int(data[key])
                break
        if not page_items or len(page_items) < page_size:
            break
        if expected_total is not None and len(items) >= expected_total:
            break
        page_index += 1
    return items


def qwb_info(
    client: CrmClient,
    term_id: int,
    class_id: int,
    record_id: int,
    user_ids: list[int],
) -> list[dict[str, Any]]:
    data = expect_success(
        client.post(
            "https://cloud-gateway.codemao.cn/crm-rocket/ranking-list/ext/userQwbInfo",
            {
                "classId": class_id,
                "checkedUserIdList": user_ids,
                "termId": term_id,
                "channelType": 1,
                "sendType": 3,
                "recordId": record_id,
                "isShowAll": True,
            },
        )
    )
    if not isinstance(data, list):
        raise SystemExit(f"Unexpected userQwbInfo response: {data!r}")
    return data


def record_content_hash(record: dict[str, Any]) -> str:
    contents = record.get("msgContents") or []
    text = "\n".join(
        str(item.get("resourceContent") or "")
        for item in contents
        if isinstance(item, dict)
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def cancel_by_msg_ids(client: CrmClient, record_id: int, msg_send_ids: list[int]) -> dict[str, Any]:
    return client.post(
        f"{client.lbk_base}/work-wechat/cancelByMsgIds",
        {"msgSendIds": msg_send_ids, "recordId": record_id, "hasLabel": "true"},
    )


def class_items(config: dict[str, Any]) -> list[dict[str, Any]]:
    return list(config.get("classes", []))


def course_id_from_args(args: argparse.Namespace) -> int | None:
    if args.course_id:
        return args.course_id
    if args.course_num:
        return 9725 + args.course_num
    return None


def find_targets(
    client: CrmClient,
    config: dict[str, Any],
    wanted_user_ids: list[int],
    course_id: int | None,
    target_hashes: dict[int, str],
    page_size: int,
    all_matches: bool,
) -> list[dict[str, Any]]:
    wanted = set(wanted_user_ids)
    targets: list[dict[str, Any]] = []
    for item in class_items(config):
        term_id = int(item["term_id"])
        class_id = int(item["class_id"])
        name = item.get("name", class_id)
        seen_user_ids: set[int] = set()
        for record in record_page(client, term_id, class_id, page_size):
            record_id = int(record["id"])
            if course_id is not None and record.get("courseId") != course_id:
                continue
            choose_ids = [int(value) for value in record.get("chooseUserList") or []]
            matched_ids = [value for value in choose_ids if not wanted or value in wanted]
            if target_hashes:
                content_hash = record_content_hash(record)
                matched_ids = [
                    value
                    for value in matched_ids
                    if not target_hashes.get(value) or target_hashes[value] == content_hash
                ]
            if not all_matches:
                matched_ids = [value for value in matched_ids if value not in seen_user_ids]
            if not matched_ids:
                continue
            seen_user_ids.update(matched_ids)
            details = qwb_info(client, term_id, class_id, record_id, matched_ids)
            msg_send_ids = [
                int(row["msgSendId"])
                for row in details
                if row.get("msgSendId") and int(row.get("userId")) in matched_ids
            ]
            if msg_send_ids:
                targets.append(
                    {
                        "className": name,
                        "termId": term_id,
                        "classId": class_id,
                        "recordId": record_id,
                        "userIds": matched_ids,
                        "msgSendIds": msg_send_ids,
                    }
                )
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.json")
    parser.add_argument("--ids", help="Optional student user IDs to match")
    parser.add_argument("--target-hashes", help="Optional JSON map of student user ID to expected message sha256")
    parser.add_argument("--course-num", type=int, help="Course number, converted with 9725 + courseNum")
    parser.add_argument("--course-id", type=int, help="CRM course ID")
    parser.add_argument("--page-size", type=int, default=20, help="Recent record rows to scan per class")
    parser.add_argument(
        "--class-spec",
        action="append",
        help="Override scan classes with classId:termId[:name]; may be repeated.",
    )
    parser.add_argument(
        "--all-matches",
        action="store_true",
        help="Cancel all matching recent records. Default is newest match per class/user.",
    )
    parser.add_argument("--record-id", type=int, action="append", help="Record ID to cancel")
    parser.add_argument("--msg-send-id", type=int, action="append", help="Msg send ID to cancel")
    parser.add_argument("--execute", action="store_true", help="Actually cancel CRM sends")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue cancelling later targets when one CRM record can no longer be cancelled.",
    )
    args = parser.parse_args()

    config = read_json(Path(args.config))
    if args.class_spec:
        classes = []
        for value in args.class_spec:
            parts = value.split(":", 2)
            if len(parts) < 2:
                raise SystemExit(f"Invalid --class-spec {value!r}; expected classId:termId[:name]")
            class_id, term_id = map(int, parts[:2])
            classes.append(
                {
                    "class_id": class_id,
                    "term_id": term_id,
                    "name": parts[2] if len(parts) == 3 else str(class_id),
                }
            )
        config["classes"] = classes
    client = CrmClient(config)
    mode = "EXECUTE" if args.execute else "DRY RUN (no cancellation will be performed)"
    print("Mode:", mode)

    if args.record_id and args.msg_send_id:
        targets = [
            {
                "className": "manual",
                "termId": None,
                "classId": None,
                "recordId": record_id,
                "userIds": [],
                "msgSendIds": args.msg_send_id,
            }
            for record_id in args.record_id
        ]
    else:
        user_ids = read_ids(Path(args.ids)) if args.ids else []
        target_hashes: dict[int, str] = {}
        if args.target_hashes:
            raw_hashes = read_json(Path(args.target_hashes))
            if not isinstance(raw_hashes, dict):
                raise SystemExit(f"Invalid --target-hashes {args.target_hashes!r}; expected JSON object")
            target_hashes = {
                int(user_id): str(message_hash)
                for user_id, message_hash in raw_hashes.items()
                if str(user_id).isdigit() and str(message_hash).strip()
            }
        course_id = course_id_from_args(args)
        print(
            f"Scanning recent records; user IDs={user_ids or 'ANY'}, "
            f"courseId={course_id or 'ANY'}, contentHashGuard={bool(target_hashes)}"
        )
        targets = find_targets(
            client,
            config,
            user_ids,
            course_id,
            target_hashes,
            args.page_size,
            args.all_matches,
        )

    if not targets:
        print("No cancel targets found.")
        return 0

    failures = 0
    canceled = 0
    already_confirmed = 0
    for target in targets:
        print(
            f"- {target['className']}: recordId={target['recordId']}, "
            f"userIds={target['userIds'] or 'manual'}, msgSendIds={target['msgSendIds']}"
        )
        if args.execute:
            try:
                response = cancel_by_msg_ids(client, int(target["recordId"]), target["msgSendIds"])
            except SystemExit as exc:
                if args.continue_on_error:
                    message = str(exc)
                    if "客户端确认发送" in message or "发送状态已变更" in message:
                        print("  skipped: 企微客户端已确认发送，CRM 不允许撤回")
                        already_confirmed += 1
                    else:
                        print(f"  cancel failed: {message}")
                        failures += 1
                    continue
                raise
            if response.get("success") is not True and response.get("code") != 200:
                if args.continue_on_error:
                    message = json.dumps(response, ensure_ascii=False)
                    if "客户端确认发送" in message or "发送状态已变更" in message:
                        print("  skipped: 企微客户端已确认发送，CRM 不允许撤回")
                        already_confirmed += 1
                    else:
                        print(f"  cancel failed: {message[:2000]}")
                        failures += 1
                    continue
                raise SystemExit(f"Cancel failed: {json.dumps(response, ensure_ascii=False)[:2000]}")
            print(f"  cancel: {response.get('msg', 'OK')}")
            canceled += 1

    if args.execute:
        print(
            "CRM cancellation requests completed: "
            f"canceled={canceled}, already_confirmed={already_confirmed}, failed={failures}."
        )
        if failures:
            return 2
    else:
        print("Dry run complete. Re-run with --execute after approval.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
