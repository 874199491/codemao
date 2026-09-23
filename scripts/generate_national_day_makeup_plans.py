#!/usr/bin/env python3
"""Batch-generate National Day makeup-plan cards for students with unfinished even lessons."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
GEN = ROOT / "scripts" / "generate_national_day_makeup_plan.py"

sys.path.insert(0, str(ROOT / "scripts"))
from teacher_workbench_config import data_prefix, script_config  # noqa: E402

LESSON_TITLE_FALLBACK = {
    2: "第2课 输出与换行",
    4: "第4课 混合运算",
    6: "第6课 计算机基础",
    8: "第8课 变量的应用",
    10: "第10课 int 和 long long",
    12: "第12课 char 和 bool",
    14: "第14课 ASCII码",
    16: "第16课 关系运算符的应用",
    18: "第18课 逻辑运算符的应用",
    20: "第20课 分支结构应用",
}


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def walk_rows(value: Any):
    if isinstance(value, list):
        yield from value
    elif isinstance(value, dict):
        for key in ("detailRows", "rows", "students", "items", "data", "list", "records"):
            child = value.get(key)
            if isinstance(child, (list, dict)):
                yield from walk_rows(child)
        if any(key in value for key in ("user_id", "userId", "course_name", "courseName", "course_number", "courseNumber", "lessonSort", "lessonName", "status")):
            yield value


def student_id(row: dict[str, Any]) -> str:
    return str(row.get("user_id") or row.get("userId") or row.get("student_id") or row.get("studentId") or "").strip()


def student_name(row: dict[str, Any]) -> str:
    return str(row.get("child_name") or row.get("childName") or row.get("student_name") or row.get("studentName") or row.get("name") or "").strip()


def course_name(row: dict[str, Any]) -> str:
    return str(row.get("course_name") or row.get("courseName") or row.get("lessonName") or "").strip()


def is_finished(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or row.get("completionStatus") or "").strip()
    if status:
        return status == "已完课"
    if "is_finish" in row:
        return bool(row.get("is_finish"))
    if "isFinish" in row:
        return bool(row.get("isFinish"))
    if "is_complete" in row:
        return bool(row.get("is_complete"))
    if "isComplete" in row:
        return bool(row.get("isComplete"))
    return False


def lesson_number_from_name(name: str) -> int | None:
    match = re.match(r"\s*(\d+)\s*[-－]", str(name or ""))
    if not match:
        return None
    return int(match.group(1))


def lesson_number(row: dict[str, Any]) -> int | None:
    for key in ("lessonSort", "lesson_sort", "course_number", "courseNumber"):
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    return lesson_number_from_name(course_name(row))


def clean_lesson_title(lesson_number: int, name: str) -> str:
    text = str(name or "").strip()
    if not text or "�" in text:
        return LESSON_TITLE_FALLBACK.get(lesson_number, f"第{lesson_number}课")
    text = re.sub(rf"^\s*{lesson_number}\s*[-－]\s*", f"第{lesson_number}课 ", text)
    if not text.startswith(f"第{lesson_number}课"):
        text = f"第{lesson_number}课 {text}"
    return text


def safe_filename(value: str) -> str:
    text = str(value or "").strip() or "未命名"
    for ch in '<>:"/\\|?*':
        text = text.replace(ch, "_")
    return text.rstrip(" .") or "未命名"


def current_even_lessons(prefix: str) -> dict[int, str]:
    lessons: dict[int, str] = {}
    for path in sorted(DATA.glob(f"{prefix}-course-*-feedback.json")):
        payload = read_json(path)
        for row in walk_rows(payload):
            if not isinstance(row, dict):
                continue
            number = lesson_number(row)
            if number is None or number % 2 != 0:
                continue
            lessons[number] = clean_lesson_title(number, course_name(row))
    if lessons:
        max_even = max(lessons)
        return {number: lessons.get(number) or LESSON_TITLE_FALLBACK.get(number, f"第{number}课") for number in range(2, max_even + 1, 2)}
    return dict(LESSON_TITLE_FALLBACK)


def group_for_holiday(lessons: list[str]) -> list[str]:
    days: list[list[str]] = [[] for _ in range(7)]
    index = 0
    for day_index in range(7):
        capacity = 2 if day_index < 3 else 1
        for _ in range(capacity):
            if index < len(lessons):
                days[day_index].append(lessons[index])
                index += 1
    if index < len(lessons):
        # More than 10 lessons: append extras to the last days in order, keeping all content visible where possible.
        day_index = 6
        while index < len(lessons):
            days[day_index].append(lessons[index])
            index += 1
            day_index = max(3, day_index - 1)
    return ["、".join(day) if day else "复习 / 机动" for day in days]


def completion_rows(prefix: str) -> list[dict[str, Any]]:
    candidates = [DATA / f"{prefix}-completion-query-latest.json", DATA / "completion-query-latest.json"]
    rows: list[dict[str, Any]] = []
    for path in candidates:
        payload = read_json(path)
        if payload is None:
            continue
        rows.extend(row for row in walk_rows(payload) if isinstance(row, dict))
        if rows:
            break
    if not rows:
        for path in sorted(DATA.glob(f"{prefix}-course-*-feedback.json")):
            payload = read_json(path)
            rows.extend(row for row in walk_rows(payload) if isinstance(row, dict))
    return rows


def build_students(rows: list[dict[str, Any]], lesson_titles: dict[int, str]) -> dict[str, dict[str, Any]]:
    students: dict[str, dict[str, Any]] = {}
    lesson_set = set(lesson_titles)
    for row in rows:
        uid = student_id(row)
        if not uid:
            continue
        info = students.setdefault(uid, {"id": uid, "name": "", "finished": {}})
        name = student_name(row)
        if name and "�" not in name and not info.get("name"):
            info["name"] = name
        number = lesson_number(row)
        if number in lesson_set:
            info["finished"][number] = bool(info["finished"].get(number)) or is_finished(row)
    return students


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DATA / "国庆补课计划")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--student-id", action="append", default=[])
    parser.add_argument("--preview-only", action="store_true", help="只生成清单，不生成图片")
    args = parser.parse_args()

    config = script_config()
    prefix = data_prefix(config)
    lesson_titles = current_even_lessons(prefix)
    rows = completion_rows(prefix)
    students = build_students(rows, lesson_titles)
    target_ids = {str(value).strip() for value in args.student_id if str(value).strip()}
    targets = []
    for uid, info in sorted(students.items(), key=lambda item: (str(item[1].get("name") or ""), item[0])):
        if target_ids and uid not in target_ids:
            continue
        unfinished = [lesson_titles[number] for number in sorted(lesson_titles) if not info.get("finished", {}).get(number)]
        if not unfinished:
            continue
        targets.append((uid, info.get("name") or uid, unfinished))
    if args.limit and args.limit > 0:
        targets = targets[: args.limit]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for index, (uid, name, unfinished) in enumerate(targets, start=1):
        lessons = group_for_holiday(unfinished)
        out = args.out_dir / f"{safe_filename(name)}_{uid}_国庆补课计划.png"
        item = {"student_id": uid, "name": name, "unfinished": unfinished, "days": lessons, "image": str(out), "image_exists": out.is_file()}
        if not args.preview_only:
            cmd = [sys.executable, str(GEN), "--name", str(name), "--lessons", "|".join(lessons), "--out", str(out)]
            result = subprocess.run(cmd, cwd=ROOT, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if result.returncode != 0:
                print(f"ERR {uid} {name}: {result.stdout[-500:]}", flush=True)
                continue
            item["image_exists"] = True
        manifest.append(item)
        if index % 10 == 0 or index == len(targets):
            print(f"{'预览' if args.preview_only else '生成'}进度 {index}/{len(targets)}", flush=True)
    manifest_path = args.out_dir / "manifest.json"
    if target_ids and not args.preview_only and manifest_path.is_file():
        existing_payload = read_json(manifest_path)
        if isinstance(existing_payload, dict) and isinstance(existing_payload.get("items"), list):
            merged: dict[str, dict[str, Any]] = {
                str(item.get("student_id")): item
                for item in existing_payload["items"]
                if isinstance(item, dict) and item.get("student_id") is not None
            }
            for item in manifest:
                merged[str(item.get("student_id"))] = item
            old_order = [str(item.get("student_id")) for item in existing_payload["items"] if isinstance(item, dict)]
            new_order = [str(item.get("student_id")) for item in manifest]
            ordered_ids = list(dict.fromkeys([*old_order, *new_order]))
            manifest = [merged[item_id] for item_id in ordered_ids if item_id in merged]
    manifest_path.write_text(json.dumps({"count": len(manifest), "items": manifest}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"生成完成：{len(manifest)} 张")
    print(f"输出目录：{args.out_dir}")
    print(f"清单：{manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
