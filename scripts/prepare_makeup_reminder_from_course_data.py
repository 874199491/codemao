#!/usr/bin/env python3
"""Prepare unfinished student IDs for CodeMao makeup reminders.

This bridges the existing codemao-course-data skill and the
codemao-makeup-reminder skill:

1. Optionally run the old completion-table sync method for one or more courses.
2. Fetch unfinished CRM user IDs for the target reminder course.
3. Write an IDs file that create_makeup_reminder.py can consume.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


DEFAULT_COURSE_DATA_SKILL = Path("C:/Users/PC/.workbuddy/skills/codemao-course-data")
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "data"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def ensure_course_config(skill_dir: Path, course_num: int) -> int:
    config_path = skill_dir / "config.json"
    config = read_json(config_path)
    courses = config.setdefault("courses", {})
    key = str(course_num)
    expected = 9725 + course_num
    current = courses.get(key)
    if current is None:
        raise SystemExit(
            f"Course {course_num} is missing from {config_path}. "
            f"Add {key}: {expected} before running."
        )
    if int(current) != expected:
        print(f"Warning: course {course_num} maps to {current}, expected {expected}. Using config value.")
    return int(current)


def run_completion_sync(skill_dir: Path, course_nums: list[int], mode: str) -> None:
    if mode == "skip":
        print("Skipping completion-table sync.")
        return

    cmd = [sys.executable, str(skill_dir / "sync.py"), *[str(course_num) for course_num in course_nums]]
    if mode == "no-push":
        cmd.append("--no-push")
    elif mode == "incremental":
        cmd.append("--incremental")

    print("Running completion-table sync:")
    print(" ".join(cmd))
    env = os.environ.copy()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    env["NO_PROXY"] = "codemao.cn,codemao.com,localhost,127.0.0.1"
    env["no_proxy"] = env["NO_PROXY"]
    result = subprocess.run(cmd, cwd=str(skill_dir), text=True, env=env)
    if result.returncode != 0:
        raise SystemExit(f"Completion sync failed with exit code {result.returncode}.")


def crm_session(config: dict[str, Any]) -> requests.Session:
    cookies_path = Path(config.get("cookies_file", ""))
    if not cookies_path.exists():
        raise SystemExit(f"Cookie file not found: {cookies_path}")
    cookies_raw = read_json(cookies_path)

    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://codecamp-crm.codemao.cn",
            "Referer": "https://codecamp-crm.codemao.cn/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "authorization_type": "3",
        }
    )
    if cookies_raw.get("internal_account_token"):
        session.cookies.set("internal_account_token", cookies_raw["internal_account_token"])
    auth = cookies_raw.get("admin-authorization", "")
    if auth:
        session.headers["Authorization"] = auth if auth.startswith("Bearer ") else "Bearer " + auth
        session.headers["admin-authorization"] = auth
    return session


def fetch_unfinished(config: dict[str, Any], course_num: int, course_id: int) -> list[dict[str, Any]]:
    session = crm_session(config)
    rows: list[dict[str, Any]] = []
    for class_item in config["classes"]:
        page = 1
        class_rows: list[dict[str, Any]] = []
        while True:
            resp = session.post(
                "https://api-codecamp-crm.codemao.cn/annual/class/course-detail",
                params={"page": page, "limit": 100},
                json={
                    "term_id": int(class_item["term_id"]),
                    "class_id": int(class_item["class_id"]),
                    "course_ids": [course_id],
                    "is_finish": False,
                    "queryType": 2,
                },
                timeout=30,
            )
            if resp.status_code == 401:
                raise SystemExit("CRM cookie expired. Refresh cookies before running.")
            if resp.status_code != 200:
                raise SystemExit(f"CRM HTTP {resp.status_code}: {resp.text[:1000]}")
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items") or (data.get("data") or {}).get("items") or []
            if not items:
                break
            for item in items:
                user_id = item.get("user_id")
                if not user_id:
                    continue
                row = {
                    "course_num": course_num,
                    "course_id": course_id,
                    "class_name": class_item.get("name", ""),
                    "term_id": int(class_item["term_id"]),
                    "class_id": int(class_item["class_id"]),
                    "user_id": int(user_id),
                    "child_name": item.get("child_name") or item.get("user_name") or "",
                }
                class_rows.append(row)
            page += 1
        print(f"- {class_item.get('name', class_item['class_id'])}: unfinished={len(class_rows)}")
        rows.extend(class_rows)
    return rows


def write_outputs(rows: list[dict[str, Any]], course_num: int, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ids_path = out_dir / f"course-{course_num}-unfinished-ids.txt"
    csv_path = out_dir / f"course-{course_num}-unfinished-detail.csv"

    seen: set[int] = set()
    ids: list[int] = []
    for row in rows:
        user_id = int(row["user_id"])
        if user_id not in seen:
            seen.add(user_id)
            ids.append(user_id)
    ids_path.write_text("\n".join(str(user_id) for user_id in ids) + "\n", encoding="utf-8")

    fieldnames = ["course_num", "course_id", "class_name", "term_id", "class_id", "user_id", "child_name"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return ids_path, csv_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("course_num", type=int, help="Course number, e.g. 48")
    parser.add_argument(
        "--course-data-skill",
        default=str(DEFAULT_COURSE_DATA_SKILL),
        help="Path to the existing codemao-course-data skill",
    )
    parser.add_argument(
        "--sync-mode",
        choices=["full", "incremental", "no-push", "skip"],
        default="full",
        help="How to run the old completion-table sync method first",
    )
    parser.add_argument(
        "--sync-course-num",
        type=int,
        action="append",
        help="Course number to update in the completion table. Repeat for multiple courses.",
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output directory")
    args = parser.parse_args()

    skill_dir = Path(args.course_data_skill)
    config_path = skill_dir / "config.json"
    if not config_path.exists():
        raise SystemExit(f"Course data skill config not found: {config_path}")

    sync_course_nums = args.sync_course_num or [args.course_num]
    for sync_course_num in sync_course_nums:
        ensure_course_config(skill_dir, sync_course_num)
    course_id = ensure_course_config(skill_dir, args.course_num)
    print(f"Completion-table courses: {sync_course_nums}; reminder course: {args.course_num}")
    run_completion_sync(skill_dir, sync_course_nums, args.sync_mode)

    config = read_json(config_path)
    print(f"Fetching unfinished CRM user IDs for course {args.course_num} (course_id={course_id})...")
    rows = fetch_unfinished(config, args.course_num, course_id)
    ids_path, csv_path = write_outputs(rows, args.course_num, Path(args.out_dir))

    print(f"Total unfinished unique IDs: {len({row['user_id'] for row in rows})}")
    print(f"IDs file: {ids_path}")
    print(f"Detail CSV: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
