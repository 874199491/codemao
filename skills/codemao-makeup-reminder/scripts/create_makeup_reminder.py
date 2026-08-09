#!/usr/bin/env python3
"""Create CodeMao CRM enterprise WeChat makeup reminder tasks.

Default mode is a live API dry run: it classifies users and resolves WeChat
recipients, but does not create send tasks unless --execute is provided.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
import gzip
import zlib
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = BASE_DIR / "config.json"

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_ids(path: Path) -> list[int]:
    text = path.read_text(encoding="utf-8-sig")
    ids = [int(match) for match in re.findall(r"\d+", text)]
    seen: set[int] = set()
    result: list[int] = []
    for user_id in ids:
        if user_id not in seen:
            seen.add(user_id)
            result.append(user_id)
    if not result:
        raise SystemExit(f"No numeric user IDs found in {path}")
    return result


def read_message(args: argparse.Namespace) -> str:
    if args.message_file:
        message = Path(args.message_file).read_text(encoding="utf-8-sig")
    else:
        message = args.message or ""
    message = message.strip()
    if not message:
        raise SystemExit("Message is required. Use --message or --message-file.")
    return message


def course_ids(args: argparse.Namespace) -> tuple[int, list[int]]:
    if args.course_id:
        course_id = args.course_id
    elif args.course_num:
        course_id = 9725 + args.course_num
    else:
        raise SystemExit("Course is required. Use --course-num or --course-id.")

    unlock_ids = args.class_unlock_course_id or [course_id, course_id - 1]
    return course_id, unlock_ids


def cookie_header(cookies: Any) -> str:
    if isinstance(cookies, dict):
        return "; ".join(f"{key}={value}" for key, value in cookies.items())
    if isinstance(cookies, list):
        return "; ".join(
            f"{item['name']}={item['value']}"
            for item in cookies
            if isinstance(item, dict) and item.get("name") and item.get("value")
        )
    raise SystemExit("Unsupported cookie file format. Expected dict or browser cookie list.")


class CrmClient:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        cookies_path = Path(config["cookies_file"])
        if not cookies_path.exists():
            raise SystemExit(f"Cookie file not found: {cookies_path}")
        cookies = read_json(cookies_path)
        api = config.get("api", {})
        self.lbk_base = api.get("lbk_base", "https://lbk-crm-teacher-web-api.codemao.cn")
        self.crm_base = api.get("crm_base", "https://api-codecamp-crm.codemao.cn")
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Cookie": cookie_header(cookies),
            "Origin": api.get("origin", "https://codecamp-crm.codemao.cn"),
            "Referer": api.get("referer", "https://codecamp-crm.codemao.cn/"),
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "authorization_type": "3",
        }
        if isinstance(cookies, dict) and cookies.get("admin-authorization"):
            self.headers["admin-authorization"] = str(cookies["admin-authorization"])
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self.headers, method="POST")
        return self._open_json(req)

    def _open_json(self, req: urllib.request.Request) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                with self.opener.open(req, timeout=60) as resp:
                    body = decode_response(resp.read(), resp.headers.get("Content-Encoding"))
                break
            except urllib.error.HTTPError as err:
                body = decode_response(err.read(), err.headers.get("Content-Encoding"))
                if err.code < 500 or attempt == 3:
                    raise SystemExit(f"HTTP {err.code} {err.reason}: {body[:1000]}") from err
                last_error = err
            except urllib.error.URLError as err:
                if attempt == 3:
                    raise SystemExit(f"Network error after {attempt} attempts: {err}") from err
                last_error = err
            print(f"CRM request failed on attempt {attempt}; retrying...")
            time.sleep(2 * attempt)
        else:
            raise SystemExit(f"Network error: {last_error}")
        try:
            return json.loads(body)
        except json.JSONDecodeError as err:
            raise SystemExit(f"Non-JSON response: {body[:1000]}") from err

    def classify_users(self, user_ids: list[int]) -> list[dict[str, Any]]:
        return self._expect_success(
            self.post(
                f"{self.lbk_base}/classShiftPool/usersClassify",
                {"classPoolId": self.config["class_pool_id"], "userIds": user_ids},
            )
        )

    def checked_user_ids(
        self, class_id: int, term_id: int, user_ids: list[int], unlock_course_ids: list[int]
    ) -> list[int]:
        payload = {
            "class_id": class_id,
            "term_id": term_id,
            "select_all": 0,
            "filtered_userId_list": [],
            "checked_userId_list": user_ids,
            "classUnlockCourseId": unlock_course_ids,
        }
        data = self._expect_success(
            self.post(
                f"{self.crm_base}/normalClass/courseDetail/userWechatInfo/user-ids",
                payload,
            )
        )
        if not isinstance(data, list):
            raise SystemExit(f"Unexpected user-ids response data: {data!r}")
        result: list[int] = []
        for item in data:
            if isinstance(item, dict):
                value = item.get("userId") or item.get("user_id") or item.get("id")
            else:
                value = item
            if value is not None:
                result.append(int(value))
        return result

    def user_wechat_info(self, class_id: int, user_ids: list[int]) -> list[dict[str, Any]]:
        defaults = self.config.get("defaults", {})
        data = self._expect_success(
            self.post(
                f"{self.lbk_base}/work-wechat/userWechatInfo",
                {
                    "classId": class_id,
                    "userIdList": user_ids,
                    "excludeTaskObjectList": defaults.get("exclude_task_object_list", []),
                },
            )
        )
        if not isinstance(data, dict) or not isinstance(data.get("userWxInfoList"), list):
            raise SystemExit(f"Unexpected userWechatInfo response data: {data!r}")
        return data["userWxInfoList"]

    def send_notify(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post(f"{self.lbk_base}/qwb/task/send/notify", payload)

    @staticmethod
    def _expect_success(response: dict[str, Any]) -> Any:
        if response.get("success") is not True and response.get("code") != 200:
            raise SystemExit(f"CRM API failed: {json.dumps(response, ensure_ascii=False)[:2000]}")
        return response.get("data")


def class_lookup(config: dict[str, Any]) -> dict[int, dict[str, Any]]:
    lookup: dict[int, dict[str, Any]] = {}
    for item in config.get("classes", []):
        lookup[int(item["class_id"])] = item
    return lookup


def decode_response(raw: bytes, content_encoding: str | None) -> str:
    encoding = (content_encoding or "").lower()
    if "gzip" in encoding:
        raw = gzip.decompress(raw)
    elif "deflate" in encoding:
        raw = zlib.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def notify_payload(
    config: dict[str, Any],
    class_item: dict[str, Any],
    users: list[dict[str, Any]],
    choose_user_list: list[int],
    message: str,
    course_id: int,
) -> dict[str, Any]:
    defaults = config.get("defaults", {})
    task_users = [
        {
            "userId": int(user["userId"]),
            "externalUserIds": user.get("externalUserIds") or [],
            "nextClassTeacherId": user.get("nextClassTeacherId"),
            "nextPackageName": user.get("nextPackageName"),
        }
        for user in users
        if user.get("externalUserIds")
    ]
    return {
        "termId": int(class_item["term_id"]),
        "classId": int(class_item["class_id"]),
        "users": task_users,
        "excludeUserList": [],
        "sendType": defaults.get("send_type", 3),
        "businessType": defaults.get("business_type", 0),
        "msgContents": [
            {
                "timeStamp": int(time.time() * 1000),
                "type": 0,
                "check": True,
                "resourceContent": message,
                "sort": 0,
            }
        ],
        "tabType": str(defaults.get("tab_type", "1")),
        "hasStudy": bool(defaults.get("has_study", True)),
        "sendWechatType": defaults.get("send_wechat_type", 0),
        "sendingObject": defaults.get("sending_object", 0),
        "atSameTimeSend": bool(defaults.get("at_same_time_send", True)),
        "excludeTaskObjectList": defaults.get("exclude_task_object_list", []),
        "chooseUserList": choose_user_list,
        "courseId": course_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.json")
    parser.add_argument("--ids", required=True, help="Text/CSV file containing student user IDs")
    parser.add_argument("--message", help="Reminder message text")
    parser.add_argument("--message-file", help="File containing reminder message text")
    parser.add_argument("--course-num", type=int, help="Course number, converted with 9725 + courseNum")
    parser.add_argument("--course-id", type=int, help="CRM course ID")
    parser.add_argument(
        "--class-unlock-course-id",
        type=int,
        action="append",
        help="Override classUnlockCourseId. Repeat for multiple IDs.",
    )
    parser.add_argument("--execute", action="store_true", help="Actually create CRM send tasks")
    parser.add_argument("--save-payload", help="Save generated notify payloads to JSON")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        raise SystemExit(f"Config not found: {config_path}. Copy config.template.json to config.json.")

    config = read_json(config_path)
    user_ids = read_ids(Path(args.ids))
    message = read_message(args)
    course_id, unlock_course_ids = course_ids(args)
    client = CrmClient(config)
    classes = class_lookup(config)

    print(f"Loaded {len(user_ids)} unique user IDs.")
    print(f"Course ID: {course_id}; classUnlockCourseId: {unlock_course_ids}")
    print("Mode:", "EXECUTE" if args.execute else "DRY RUN (no notify task will be created)")

    grouped = client.classify_users(user_ids)
    payloads: list[dict[str, Any]] = []
    total_task_users = 0

    for group in grouped:
        class_id = int(group["classId"])
        class_item = classes.get(class_id)
        if not class_item:
            print(f"Skip unknown classId={class_id}, users={len(group.get('userIds', []))}")
            continue

        raw_ids = [int(item) for item in group.get("userIds", [])]
        checked_ids = client.checked_user_ids(
            class_id, int(class_item["term_id"]), raw_ids, unlock_course_ids
        )
        wx_users = client.user_wechat_info(class_id, checked_ids)
        payload = notify_payload(config, class_item, wx_users, checked_ids, message, course_id)
        payloads.append(payload)
        total_task_users += len(payload["users"])

        print(
            f"- {class_item.get('name', class_id)}: imported={len(raw_ids)}, "
            f"checked={len(checked_ids)}, sendable={len(payload['users'])}"
        )

        if args.execute:
            response = client.send_notify(payload)
            if response.get("success") is not True and response.get("code") != 200:
                raise SystemExit(f"Notify failed: {json.dumps(response, ensure_ascii=False)[:2000]}")
            print(f"  notify: {response.get('msg', 'OK')}")

    if args.save_payload:
        Path(args.save_payload).write_text(
            json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Saved payloads: {args.save_payload}")

    print(f"Prepared {len(payloads)} class task(s), {total_task_users} sendable parent contact(s).")
    if not args.execute:
        print("Dry run complete. Re-run with --execute after the teacher approves.")
    else:
        print("CRM tasks created. The teacher must confirm final sending in enterprise WeChat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
