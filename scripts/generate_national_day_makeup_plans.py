#!/usr/bin/env python3
"""Batch-generate National Day makeup-plan cards for students with unfinished even lessons."""
from __future__ import annotations

import argparse
import csv
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
    """Return the real lesson number.

    The real lesson number comes from the course-name prefix (e.g. "12-char 和 bool"
    -> 12), because the physical course_number shifts by one whenever a training
    (赛考精讲) lesson is inserted. Falls back to the physical course_number.
    """
    from_name = lesson_number_from_name(course_name(row))
    if from_name is not None:
        return from_name
    for key in ("lessonSort", "lesson_sort", "course_number", "courseNumber"):
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    return None


def training_course_numbers(config: dict[str, Any]) -> set[int]:
    if not config.get("has_exam_training_lessons", False):
        return set()
    numbers = set()
    for value in config.get("training_course_numbers") or []:
        try:
            numbers.add(int(value))
        except (TypeError, ValueError):
            pass
    return numbers


def is_training_course(number: int | None, name: str, training_numbers: set[int]) -> bool:
    if number is None:
        return False
    title_number = lesson_number_from_name(name)
    return title_number in training_numbers or "赛考精讲" in str(name or "")


def max_unlocked_lesson(payload: Any) -> int:
    """Return the highest *real* lesson number that is already unlocked.

    The payload's maxLesson/currentCourseSort use physical course numbers, so we
    translate the physical cutoff into a real lesson number via the course names.
    """
    physical_max = 0
    if isinstance(payload, dict):
        for key in ("maxLesson", "max_lesson"):
            try:
                value = int(payload.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                physical_max = value
                break
        if not physical_max:
            current_sorts = []
            for result in payload.get("classResults") or []:
                if not isinstance(result, dict):
                    continue
                class_info = result.get("classInfo") or {}
                try:
                    current_sorts.append(int(class_info.get("currentCourseSort") or 0))
                except (TypeError, ValueError):
                    pass
            positive_sorts = [value for value in current_sorts if value > 0]
            if positive_sorts:
                physical_max = min(positive_sorts)
    rows = [row for row in walk_rows(payload) if isinstance(row, dict)]
    if physical_max > 0:
        real_numbers: list[int] = []
        for row in rows:
            try:
                physical = int(row.get("course_number") or row.get("courseNumber") or 0)
            except (TypeError, ValueError):
                physical = 0
            if physical and physical <= physical_max:
                number = lesson_number(row)
                if number is not None:
                    real_numbers.append(number)
        if real_numbers:
            return max(real_numbers)
    lesson_numbers = [number for number in (lesson_number(row) for row in rows) if number is not None]
    return max(lesson_numbers or [0])


def clean_lesson_title(lesson_number: int, name: str) -> str:
    text = str(name or "").strip()
    if not text or "�" in text:
        return LESSON_TITLE_FALLBACK.get(lesson_number, f"第{lesson_number}课")
    text = re.sub(r"^\s*\d+\s*[-－]\s*", f"第{lesson_number}课 ", text)
    if not text.startswith(f"第{lesson_number}课"):
        text = f"第{lesson_number}课 {text}"
    return text


def safe_filename(value: str) -> str:
    text = str(value or "").strip() or "未命名"
    for ch in '<>:"/\\|?*':
        text = text.replace(ch, "_")
    return text.rstrip(" .") or "未命名"


def completion_payload(prefix: str) -> Any:
    for path in (DATA / f"{prefix}-completion-query-latest.json", DATA / "completion-query-latest.json"):
        payload = read_json(path)
        if payload is not None:
            return payload
    return None


def refunded_student_ids(prefix: str) -> set[str]:
    """Union of already-refunded student ids (manual list + CRM-fetched list)."""
    ids: set[str] = set()
    paths = [
        DATA / f"{prefix}-refunded-students.json",
        DATA / f"{prefix}-confirmed-refunded-students.json",
        DATA / "new-class-refunded-students.json",
    ]
    for path in paths:
        payload = read_json(path)
        if payload is None:
            continue
        candidates: Any = []
        if isinstance(payload, list):
            candidates = payload
        elif isinstance(payload, dict):
            if isinstance(payload.get("students"), list):
                candidates = payload["students"]
            elif isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("items"), list):
                candidates = payload["data"]["items"]
            elif isinstance(payload.get("items"), list):
                candidates = payload["items"]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            uid = student_id(item)
            if uid:
                ids.add(uid)
    return ids


def active_student_ids(config: dict[str, Any]) -> set[str]:
    """Current enrolled student ids from the configured roster CSV.

    Refunded / removed students are absent from the current roster, so this keeps
    the makeup plan limited to students who are actually still enrolled.
    """
    ids: set[str] = set()
    try:
        from teacher_workbench_config import data_path  # noqa: PLC0415

        path = data_path("roster_csv", config)
    except Exception:
        return ids
    if not path.is_file():
        return ids
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                uid = str(row.get("学生ID") or row.get("学生id") or row.get("student_id") or "").strip()
                if uid:
                    ids.add(uid)
    except OSError:
        return set()
    return ids


def current_even_lessons(prefix: str, payload: Any, config: dict[str, Any]) -> dict[int, str]:
    lessons: dict[int, str] = {}
    training_numbers = training_course_numbers(config)
    unlocked = max_unlocked_lesson(payload)
    sources = [payload] if payload is not None else [read_json(path) for path in sorted(DATA.glob(f"{prefix}-course-*-feedback.json"))]
    for source in sources:
        for row in walk_rows(source):
            if not isinstance(row, dict):
                continue
            number = lesson_number(row)
            name = course_name(row)
            if number is None or number % 2 != 0:
                continue
            if unlocked and number > unlocked:
                continue
            if is_training_course(number, name, training_numbers):
                continue
            lessons[number] = clean_lesson_title(number, name)
    if lessons:
        max_even = max(number for number in lessons if not unlocked or number <= unlocked)
        return {
            number: lessons.get(number) or LESSON_TITLE_FALLBACK.get(number, f"第{number}课")
            for number in range(2, max_even + 1, 2)
            if not is_training_course(number, lessons.get(number, ""), training_numbers)
        }
    max_even = unlocked if unlocked else max(LESSON_TITLE_FALLBACK)
    return {
        number: title
        for number, title in LESSON_TITLE_FALLBACK.items()
        if number <= max_even and not is_training_course(number, title, training_numbers)
    }


def group_for_holiday(lessons: list[str]) -> list[str]:
    """Distribute lessons over the 7 holiday days.

    - 7 or fewer lessons: one lesson per day.
    - More than 7 lessons: one per day, but the first three days take two each.
    """
    days: list[list[str]] = [[] for _ in range(7)]
    double_first_three = len(lessons) > 7
    index = 0
    for day_index in range(7):
        capacity = 2 if (double_first_three and day_index < 3) else 1
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
    return ["、".join(day) if day else "好好休息" for day in days]


def completion_rows(prefix: str, payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(row for row in walk_rows(payload) if isinstance(row, dict))
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
    payload = completion_payload(prefix)
    lesson_titles = current_even_lessons(prefix, payload, config)
    rows = completion_rows(prefix, payload)
    students = build_students(rows, lesson_titles)
    refunded = refunded_student_ids(prefix)
    active = active_student_ids(config)
    target_ids = {str(value).strip() for value in args.student_id if str(value).strip()}
    targets = []
    refunded_skipped = 0
    inactive_skipped = 0
    for uid, info in sorted(students.items(), key=lambda item: (str(item[1].get("name") or ""), item[0])):
        if target_ids and uid not in target_ids:
            continue
        if active and uid not in active:
            inactive_skipped += 1
            continue
        if uid in refunded:
            refunded_skipped += 1
            continue
        unfinished = [lesson_titles[number] for number in sorted(lesson_titles) if not info.get("finished", {}).get(number)]
        if not unfinished:
            continue
        targets.append((uid, info.get("name") or uid, unfinished))
    if inactive_skipped:
        print(f"已跳过非在读学员（含已退费）{inactive_skipped} 人。", flush=True)
    if refunded_skipped:
        print(f"已跳过退费名单学员 {refunded_skipped} 人。", flush=True)
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
