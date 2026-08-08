#!/usr/bin/env python3
"""Build a workbench profile from a CRM network capture and DingTalk sheet link."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_WORKSPACE = SCRIPT_DIR.parents[0]
CLASS_COLUMNS = [
    "beisen_user_fullname",
    "worker_no",
    "teacher_email",
    "class_id",
    "class_name",
    "term_id",
    "term_name",
    "package_id",
    "package_name",
    "current_new_course_sort",
    "current_new_course_name",
    "current_user_cnt",
    "renew_denominator_cnt",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-prefix", required=True)
    parser.add_argument("--capture-jsonl", type=Path, required=True)
    parser.add_argument("--slice-id", type=int, default=3191)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--node-id", default="")
    parser.add_argument("--dingtalk-url", default="")
    parser.add_argument("--learning-sheet-id", default="")
    parser.add_argument("--learning-sheet-name", default="")
    parser.add_argument("--auto-learning-sheet", action="store_true")
    parser.add_argument("--learning-sheet-range", default="A1:AZ300")
    parser.add_argument("--class-pool-id", type=int, default=0)
    parser.add_argument("--update-config", type=Path)
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def collect_dicts(value: Any, rows: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        keys = {str(key) for key in value}
        has_class_id = bool({"classId", "class_id", "class_id_str", "id"} & keys)
        has_class_name = bool({"className", "class_name", "class_name_str", "name"} & keys)
        has_term = bool({"termName", "term_name", "termId", "term_id"} & keys)
        if has_class_id and (has_class_name or has_term):
            rows.append(value)
        for child in value.values():
            collect_dicts(child, rows)
    elif isinstance(value, list):
        for child in value:
            collect_dicts(child, rows)


def pick(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in {None, ""}:
            return str(value).strip()
    return ""


def normalize_class_row(row: dict[str, Any]) -> dict[str, str] | None:
    class_id = pick(row, "class_id", "classId", "class_id_str", "id")
    class_name = pick(row, "class_name", "className", "class_name_str", "name")
    term_id = pick(row, "term_id", "termId")
    term_name = pick(row, "term_name", "termName", "term_name_str")
    package_id = pick(row, "package_id", "packageId")
    package_name = pick(row, "package_name", "packageName")
    if not class_id or not class_id.isdigit() or (not class_name and not term_name):
        return None
    if not any((term_id, term_name, package_id, package_name)):
        return None
    return {
        "beisen_user_fullname": pick(row, "beisen_user_fullname", "teacherName", "teacher_name"),
        "worker_no": pick(row, "worker_no", "workerNo"),
        "teacher_email": pick(row, "teacher_email", "teacherEmail"),
        "class_id": class_id,
        "class_name": class_name,
        "term_id": term_id,
        "term_name": term_name,
        "package_id": package_id,
        "package_name": package_name,
        "current_new_course_sort": pick(row, "current_new_course_sort", "currentCourseSort"),
        "current_new_course_name": pick(row, "current_new_course_name", "currentCourseName"),
        "current_user_cnt": pick(row, "current_user_cnt", "currentUserCount", "studentCount"),
        "renew_denominator_cnt": pick(row, "renew_denominator_cnt", "renewDenominatorCount"),
    }


def fallback_extract_classes(capture_jsonl: Path, class_csv: Path) -> int:
    candidates: list[dict[str, Any]] = []
    for line in capture_jsonl.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("event") != "body":
            continue
        collect_dicts(record.get("responseBody"), candidates)
    deduped: dict[str, dict[str, str]] = {}
    for row in candidates:
        normalized = normalize_class_row(row)
        if normalized:
            deduped[normalized["class_id"]] = normalized
    rows = list(deduped.values())
    rows = filter_primary_package(rows)
    if not rows:
        return 0
    class_csv.parent.mkdir(parents=True, exist_ok=True)
    with class_csv.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=CLASS_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"fallback_class_extract": str(class_csv), "rowCount": len(rows)}, ensure_ascii=False, indent=2))
    return len(rows)


def filter_primary_package(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep the dominant course package when one CRM capture mixes old cohorts."""
    counts = Counter(row.get("package_id", "").strip() for row in rows if row.get("package_id", "").strip())
    if len(counts) <= 1:
        return rows
    primary, primary_count = counts.most_common(1)[0]
    if primary_count < 2:
        return rows
    return [row for row in rows if row.get("package_id", "").strip() == primary]


def filter_class_csv_in_place(class_csv: Path) -> None:
    if not class_csv.exists():
        return
    with class_csv.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        rows = [dict(row) for row in reader]
        fieldnames = list(reader.fieldnames or CLASS_COLUMNS)
    filtered = filter_primary_package(rows)
    if len(filtered) == len(rows):
        return
    with class_csv.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(filtered)
    print(
        json.dumps(
            {
                "filtered_class_csv": str(class_csv),
                "before": len(rows),
                "after": len(filtered),
                "reason": "kept dominant package_id to avoid mixed old cohorts",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def collect_class_pool_ids(value: Any, ids: list[int]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) == "classPoolId":
                try:
                    class_pool_id = int(child)
                except (TypeError, ValueError):
                    class_pool_id = 0
                if class_pool_id > 0:
                    ids.append(class_pool_id)
            collect_class_pool_ids(child, ids)
    elif isinstance(value, list):
        for child in value:
            collect_class_pool_ids(child, ids)


def extract_class_pool_id(capture_jsonl: Path) -> int:
    ids: list[int] = []
    for line in capture_jsonl.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        collect_class_pool_ids(record, ids)
    if not ids:
        return 0
    counts: dict[int, int] = {}
    for value in ids:
        counts[value] = counts.get(value, 0) + 1
    return max(counts, key=counts.get)


def extract_classes(args: argparse.Namespace, class_csv: Path) -> None:
    extractor = args.workspace / "scripts" / "extract_superset_teacher_class_detail.mjs"
    if extractor.exists():
        result = subprocess.run(
            [
                "node",
                str(extractor),
                str(args.capture_jsonl),
                str(class_csv),
                str(args.slice_id),
            ],
            cwd=args.workspace,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if result.returncode == 0:
            print(result.stdout)
            filter_class_csv_in_place(class_csv)
            return
        print("Superset extractor failed; trying generic CRM response fallback.", file=sys.stderr)
        print(result.stdout[-2000:], file=sys.stderr)
    if fallback_extract_classes(args.capture_jsonl, class_csv) <= 0:
        raise RuntimeError(
            "Could not extract CRM classes from capture. Start listening, then refresh the target teacher's class dashboard."
        )


def main() -> int:
    args = parse_args()
    class_csv = args.workspace / "data" / f"{args.data_prefix}-completion-classes.csv"
    extract_classes(args, class_csv)
    class_pool_id = int(args.class_pool_id or 0) or extract_class_pool_id(args.capture_jsonl)
    command = [
        sys.executable,
        str(SCRIPT_DIR / "generate_profile.py"),
        "--data-prefix",
        args.data_prefix,
        "--class-file",
        str(class_csv),
        "--workspace",
        str(args.workspace),
        "--completion-classes-csv",
        str(class_csv.relative_to(args.workspace)),
        "--learning-sheet-range",
        args.learning_sheet_range,
    ]
    if class_pool_id > 0:
        command.extend(["--class-pool-id", str(class_pool_id)])
    if args.node_id:
        command.extend(["--node-id", args.node_id])
    if args.dingtalk_url:
        command.extend(["--dingtalk-url", args.dingtalk_url])
    if args.learning_sheet_id:
        command.extend(["--learning-sheet-id", args.learning_sheet_id])
    if args.learning_sheet_name:
        command.extend(["--learning-sheet-name", args.learning_sheet_name])
    if args.auto_learning_sheet or not args.learning_sheet_id:
        command.append("--auto-learning-sheet")
    if args.update_config:
        command.extend(["--update-config", str(args.update_config)])
    if args.out:
        command.extend(["--out", str(args.out)])
    subprocess.run(command, cwd=args.workspace, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
