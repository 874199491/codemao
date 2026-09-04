#!/usr/bin/env python3
"""Run configured weekly operations for the CodeMao teacher workbench template."""

from __future__ import annotations

import argparse
import csv
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from teacher_workbench_config import class_mappings, data_path, data_prefix, load_workbench_config, script_config
from week_context import WeekContext, context_for


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


WORKSPACE = Path(__file__).resolve().parents[1]
DATA = WORKSPACE / "data"
SCRIPTS = WORKSPACE / "scripts"
SCRIPT_CONFIG = script_config()
WORKBENCH_CONFIG = load_workbench_config()
PREFIX = data_prefix(SCRIPT_CONFIG)
PORT = int(WORKBENCH_CONFIG.get("chrome_debug_port") or 9223)
CLASSES = tuple((str(class_id), label) for class_id, label in class_mappings(SCRIPT_CONFIG))


def clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        env.pop(name, None)
    return env


def run(command: list[str], retry: int = 0) -> None:
    print("$ " + " ".join(command), flush=True)
    attempts = retry + 1
    for attempt in range(attempts):
        try:
            subprocess.run(
                command,
                cwd=WORKSPACE,
                env=clean_env(),
                check=True,
                text=True,
            )
            return
        except subprocess.CalledProcessError:
            if attempt >= attempts - 1:
                raise
            print(f"  重试 {attempt + 1}/{attempts - 1}（上一次命令非零退出）", flush=True)


def run_parallel(commands: list[list[str]]) -> None:
    if not commands:
        return
    if len(commands) == 1:
        run(commands[0])
        return
    for command in commands:
        print("$ " + " ".join(command), flush=True)

    def worker(command: list[str]) -> None:
        subprocess.run(
            command,
            cwd=WORKSPACE,
            env=clean_env(),
            check=True,
            text=True,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(commands)) as executor:
        futures = [executor.submit(worker, command) for command in commands]
        for future in concurrent.futures.as_completed(futures):
            future.result()


def require_logged_in_crm() -> None:
    try:
        with urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=2) as response:
            targets = json.load(response)
    except (OSError, URLError, json.JSONDecodeError) as error:
        raise RuntimeError("Chrome 9223 不可用；已停止，未写入钉钉。") from error
    crm_pages = [
        item
        for item in targets
        if item.get("type") == "page"
        and "codecamp-crm.codemao.cn" in str(item.get("url") or "")
    ]
    authenticated = [
        item
        for item in crm_pages
        if "/not_login" not in str(item.get("url") or "")
        and "login" not in str(item.get("url") or "").lower()
        and item.get("webSocketDebuggerUrl")
    ]
    if not authenticated:
        raise RuntimeError("9223 中没有已登录的 CRM 页面；已停止，未使用旧数据写入钉钉。")
    print(f"CRM 登录检查通过：{authenticated[0].get('url')}", flush=True)


def completion_json() -> Path:
    return DATA / f"{PREFIX}-completion-query-latest.json"


def weekly_completion_json(context: WeekContext) -> Path:
    return DATA / f"{PREFIX}-week{context.week}-completion-query-latest.json"


def completion_csv() -> Path:
    return DATA / f"{PREFIX}-completion-query-latest.csv"


def completion_student_snapshot_ready(path: Path) -> bool:
    """Return whether the cached CRM student snapshot covers configured classes."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return False
    snapshot_class_ids = {
        str((row.get("classInfo") or {}).get("classId") or "")
        for row in rows
        if isinstance(row, dict)
    }
    configured_class_ids = {class_id for class_id, _ in CLASSES}
    return bool(configured_class_ids) and configured_class_ids.issubset(snapshot_class_ids)


def largest_numeric_cluster(values: list[int], max_gap: int) -> list[int]:
    if len(values) < 3:
        return values
    ordered = sorted(set(values))
    clusters: list[list[int]] = [[ordered[0]]]
    for value in ordered[1:]:
        if value - clusters[-1][-1] <= max_gap:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return max(clusters, key=len)


def ensure_configured_classes_are_coherent() -> None:
    class_ids = [int(class_id) for class_id, _ in CLASSES if str(class_id).isdigit()]
    if len(class_ids) != 3 or len(set(class_ids)) != 3:
        raise RuntimeError(
            "每位老师每一期必须配置且只配置 3 个授课班级（周五、周六午、周六晚）。"
            f"当前配置班级：{class_ids}。请重新生成老师配置后再更新。"
        )
    labels = [str(label).strip() for _, label in CLASSES]
    schedule_matches = {
        "周五": sum(label.startswith("周五") for label in labels),
        "周六午": sum(label.startswith("周六午") for label in labels),
        "周六晚": sum(label.startswith("周六晚") for label in labels),
    }
    if any(count != 1 for count in schedule_matches.values()):
        raise RuntimeError(
            "三个授课班级必须分别对应周五、周六午、周六晚。"
            f"当前标签：{labels}。请在配置面板核对班级。"
        )
    primary = set(largest_numeric_cluster(class_ids, 1000))
    outliers = [class_id for class_id in class_ids if class_id not in primary]
    if outliers and len(primary) >= 2:
        raise RuntimeError(
            "当前老师配置的 CRM 班级疑似混入其他班期/旧班级，已停止，未写入钉钉。"
            f"主班级：{sorted(primary)}；异常班级：{sorted(outliers)}。"
            "请在配置面板重新生成老师配置，或删除异常班级后再更新完课数据。"
        )


def keep_only_configured_completion_classes(classes_csv: Path) -> None:
    """Remove stale/other-cohort rows from the runtime class CSV."""
    with classes_csv.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if "class_id" not in fieldnames:
        raise RuntimeError(f"完课班级文件缺少 class_id 列：{classes_csv}")
    configured_order = [str(class_id) for class_id, _ in CLASSES]
    configured_ids = set(configured_order)
    by_id: dict[str, dict[str, str]] = {}
    for row in rows:
        class_id = str(row.get("class_id") or "").strip()
        if class_id in configured_ids and class_id not in by_id:
            by_id[class_id] = row
    missing = [class_id for class_id in configured_order if class_id not in by_id]
    if missing:
        raise RuntimeError(
            f"完课班级文件缺少配置中的班级：{missing}。请重新生成老师配置；已停止，未写入钉钉。"
        )
    filtered = [by_id[class_id] for class_id in configured_order]
    if len(rows) == len(filtered) and all(
        str(row.get("class_id") or "").strip() == configured_order[index]
        for index, row in enumerate(rows)
    ):
        return
    temporary = classes_csv.with_suffix(classes_csv.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(filtered)
    temporary.replace(classes_csv)
    removed = len(rows) - len(filtered)
    print(
        f"已按老师配置整理完课班级文件：保留 3 个授课班，移除 {max(0, removed)} 条旧班/其他班期记录。",
        flush=True,
    )


def ensure_completion_student_snapshot() -> Path:
    """Refresh the CRM student baseline when the configured cache is missing or stale."""
    ensure_configured_classes_are_coherent()
    classes_csv = data_path("completion_classes_csv", SCRIPT_CONFIG)
    students_json = data_path("students_json", SCRIPT_CONFIG)
    if not classes_csv.exists() or classes_csv.stat().st_size == 0:
        raise RuntimeError(
            f"完课班级配置文件不存在或为空：{classes_csv}。"
            "请先在配置面板重新生成老师配置；已停止，未写入钉钉。"
        )
    keep_only_configured_completion_classes(classes_csv)
    if completion_student_snapshot_ready(students_json):
        return students_json

    print(
        f"CRM 学员基础数据缺失、为空或不属于当前配置班级，正在自动刷新：{students_json}",
        flush=True,
    )
    run(
        [
            "node",
            str(SCRIPTS / "fetch_group_completion_from_crm.mjs"),
            "--port",
            str(PORT),
            "--classes-csv",
            str(classes_csv),
            "--out-json",
            str(students_json),
            "--out-csv",
            str(students_json.with_suffix(".csv")),
        ]
    )
    if not completion_student_snapshot_ready(students_json):
        raise RuntimeError(
            f"已从 CRM 刷新学员基础数据，但文件仍未覆盖当前配置班级：{students_json}。"
            "已停止，未写入钉钉。"
        )
    print(f"CRM 学员基础数据刷新完成：{students_json}", flush=True)
    return students_json


def live_json(context: WeekContext) -> Path:
    return DATA / f"{PREFIX}-week{context.week}-live-absent-latest.json"


def live_csv(context: WeekContext) -> Path:
    return DATA / f"{PREFIX}-week{context.week}-live-absent-latest.csv"


def solitaire_json(context: WeekContext, schedule: str) -> Path:
    return DATA / f"{PREFIX}-week{context.week}-solitaire-{schedule}" / "latest.json"


def feedback_course_files(context: WeekContext) -> list[Path]:
    return [
        DATA / f"{PREFIX}-course-{context.first_course}-feedback.json",
        DATA / f"{PREFIX}-course-{context.second_course}-feedback.json",
    ]


def feedback_csv(context: WeekContext) -> Path:
    if context.week == 1:
        return DATA / f"{PREFIX}-post-class-feedback.csv"
    return DATA / f"{PREFIX}-week{context.week}-post-class-feedback.csv"


def persist_context(context: WeekContext) -> None:
    payload = {
        "week": context.week,
        "start": context.start.isoformat(),
        "end": context.end.isoformat(),
        "courses": [context.first_course, context.second_course],
    }
    (DATA / f"{PREFIX}-latest-week-context.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"当前更新周次：W{context.week}（{context.start} 至 {context.end}），"
        f"课程：第{context.first_course}、{context.second_course}课",
        flush=True,
    )


def ensure_week_columns(context: WeekContext) -> None:
    run(
        [
            "py",
            "-3.10",
            str(SCRIPTS / "ensure_week_learning_columns.py"),
            "--week",
            str(context.week),
        ]
    )


def fetch_completion(context: WeekContext) -> None:
    students_json = ensure_completion_student_snapshot()
    training_args = (
        ["--exclude-training-lessons"]
        if bool(WORKBENCH_CONFIG.get("has_exam_training_lessons", False))
        else []
    )
    run(
        [
            "node",
            str(SCRIPTS / "fetch_group_lesson_completion_from_crm.mjs"),
            "--port",
            str(PORT),
            "--runtime-timeout-ms",
            "900000",
            "--max-lesson",
            str(context.second_course),
            "--class-concurrency",
            str(int((WORKBENCH_CONFIG.get("completion") or {}).get("class_concurrency") or 3)),
            "--classes-csv",
            str(data_path("completion_classes_csv", SCRIPT_CONFIG)),
            "--students-json",
            str(students_json),
            "--out-json",
            str(completion_json()),
            "--out-csv",
            str(completion_csv()),
            *training_args,
        ]
    )
    payload = json.loads(completion_json().read_text(encoding="utf-8"))
    summaries = payload.get("summaries") or []
    expected_class_count = len(CLASSES)
    configured_class_ids = {str(class_id) for class_id, _ in CLASSES}
    configured_summaries = [
        item
        for item in summaries
        if str(item.get("classId") or item.get("class_id") or "") in configured_class_ids
    ]
    returned_configured_ids = {
        str(item.get("classId") or item.get("class_id") or "")
        for item in configured_summaries
    }
    missing_configured_ids = sorted(configured_class_ids - returned_configured_ids)
    detail_rows = payload.get("detailRows") or []

    def complete_historical_cache(item: dict[str, object]) -> bool:
        skipped = str(item.get("skipped") or "")
        if context.end >= date.today() or not skipped.startswith("fetch_failed_used_previous:"):
            return False
        class_id = str(item.get("classId") or item.get("class_id") or "")
        expected_students = int(item.get("studentCount") or 0)
        if not class_id or expected_students < 1:
            return False
        for lesson in (context.first_course, context.second_course):
            covered_students = {
                str(row.get("userId") or row.get("user_id") or "")
                for row in detail_rows
                if str(row.get("classId") or row.get("class_id") or "") == class_id
                and int(row.get("lessonSort") or row.get("lesson_sort") or 0) == lesson
                and str(row.get("status") or "") in {"已完课", "到课未完课", "未完课"}
            }
            covered_students.discard("")
            if len(covered_students) < expected_students:
                return False
        return True

    accepted_cached = [
        item for item in configured_summaries if item.get("skipped") and complete_historical_cache(item)
    ]
    accepted_cached_ids = {
        str(item.get("classId") or item.get("class_id") or "") for item in accepted_cached
    }
    configured_skipped = [
        item
        for item in configured_summaries
        if item.get("skipped")
        and str(item.get("classId") or item.get("class_id") or "") not in accepted_cached_ids
    ]
    extra_summaries = [
        item
        for item in summaries
        if str(item.get("classId") or item.get("class_id") or "") not in configured_class_ids
    ]
    extra_skipped = [item for item in extra_summaries if item.get("skipped")]
    incomplete = (
        len(configured_summaries) != expected_class_count
        or missing_configured_ids
        or configured_skipped
        or int(payload.get("maxLesson") or 0) < context.second_course
    )
    if incomplete:
        skipped_text = "；".join(
            f"{item.get('classId') or item.get('class_id')}/{item.get('termId') or item.get('term_id')}: {str(item.get('skipped'))[:160]}"
            for item in configured_skipped
        )
        raise RuntimeError(
            f"配置的 {expected_class_count} 个班最新周完课数据未完整返回；"
            f"配置内实际返回 {len(configured_summaries)} 个，"
            f"总返回 {len(summaries)} 个，classCount={payload.get('classCount')}，"
            f"maxLesson={payload.get('maxLesson')}，目标课程到第 {context.second_course} 课。"
            + (f" 缺失配置班级：{','.join(missing_configured_ids)}。" if missing_configured_ids else "")
            + (f" 跳过/失败班级：{skipped_text}" if skipped_text else "")
            + " 已停止，未写入钉钉。"
        )
    if accepted_cached:
        payload["acceptedHistoricalCompletionCache"] = [
            {
                "classId": item.get("classId") or item.get("class_id"),
                "termId": item.get("termId") or item.get("term_id"),
                "studentCount": item.get("studentCount"),
                "targetCourses": [context.first_course, context.second_course],
            }
            for item in accepted_cached
        ]
        print(
            "警告：CRM 串行重试后仍失败，但所选历史周缓存已完整覆盖目标课程和全部学员，允许继续："
            + json.dumps(payload["acceptedHistoricalCompletionCache"], ensure_ascii=False),
            flush=True,
        )
    if extra_skipped:
        payload["ignoredExtraCompletionFailures"] = [
            {
                "classId": item.get("classId") or item.get("class_id"),
                "termId": item.get("termId") or item.get("term_id"),
                "skipped": str(item.get("skipped"))[:300],
            }
            for item in extra_skipped
        ]
        print(
            "警告：CRM 返回了配置外班级的完课失败记录，已忽略，不影响配置内班级写入："
            + json.dumps(payload["ignoredExtraCompletionFailures"], ensure_ascii=False),
            flush=True,
        )
    payload["targetWeek"] = context.week
    payload["targetCourses"] = [context.first_course, context.second_course]
    payload["weekStart"] = context.start.isoformat()
    payload["weekEnd"] = context.end.isoformat()
    completion_json().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    weekly_completion_json(context).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_completion(
    context: WeekContext,
    *,
    ensure_columns: bool = True,
    sync_makeup: bool = True,
) -> None:
    if ensure_columns:
        ensure_week_columns(context)
    run(
        [
            "py",
            "-3.10",
            str(SCRIPTS / "update_completion_batch.py"),
            "--completion-json",
            str(completion_json()),
            "--week",
            str(context.week),
        ]
    )
    if sync_makeup:
        run(
            [
                "py",
                "-3.10",
                str(SCRIPTS / "sync_makeup_sheet.py"),
                "--week",
                str(context.week),
            ]
        )
        run(["py", "-3.10", str(SCRIPTS / "format_makeup_sheet.py")])
    else:
        print(f"跳过补课表同步：W{context.week} 不是本次选择的最新周。", flush=True)


def fetch_live(context: WeekContext) -> None:
    # The CRM live-board endpoint filters by a rolling lookback window. A fixed
    # 14-day window cannot find historical weeks, so extend it back to the
    # target week and keep a seven-day buffer for delayed/shifted live boards.
    live_lookback_days = max(14, (date.today() - context.start).days + 7)
    print(
        f"直播查询范围：最近 {live_lookback_days} 天（覆盖 W{context.week}：{context.start} 至 {context.end}）",
        flush=True,
    )
    run(
        [
            "node",
            str(SCRIPTS / "fetch_live_absence_from_crm.mjs"),
            "--port",
            str(PORT),
            "--course-num",
            str(context.first_course),
            "--days",
            str(live_lookback_days),
            "--out-json",
            str(live_json(context)),
            "--out-csv",
            str(live_csv(context)),
        ],
        retry=2,
    )


def write_live(
    context: WeekContext,
    *,
    ensure_columns: bool = True,
) -> None:
    if ensure_columns:
        ensure_week_columns(context)
    run(
        [
            "py",
            "-3.10",
            str(SCRIPTS / "update_live_participation_batch.py"),
            "--absence-json",
            str(live_json(context)),
            "--week",
            str(context.week),
        ]
    )


def update_completion_and_live(context: WeekContext, *, sync_makeup: bool = True) -> None:
    ensure_week_columns(context)
    run_parallel(
        [
            [
                "py",
                "-3.10",
                str(Path(__file__).resolve()),
                "completion",
                "--week",
                str(context.week),
                "--fetch-only",
            ],
            [
                "py",
                "-3.10",
                str(Path(__file__).resolve()),
                "live",
                "--week",
                str(context.week),
                "--fetch-only",
            ],
        ]
    )
    write_completion(context, ensure_columns=False, sync_makeup=sync_makeup)
    write_live(context, ensure_columns=False)


def solitaire_class_code() -> str:
    profile = WORKBENCH_CONFIG.get("profile") if isinstance(WORKBENCH_CONFIG.get("profile"), dict) else {}
    profile_values = (
        profile.get("solitaire_class_code"),
        profile.get("class_code"),
        profile.get("data_prefix"),
        profile.get("cohort_code"),
    )
    for value in profile_values:
        text = str(value or "").strip()
        if text and text.lower() != "demo":
            return text
    if profile:
        raise RuntimeError(
            "缺少有效的接龙群搜索班期标识：当前老师 profile.data_prefix 仍是 demo 或为空。"
            "请先在配置面板重新生成老师配置，确保 profile.data_prefix 为真实班期，例如 0807。"
        )

    for value in (
        WORKBENCH_CONFIG.get("solitaire_class_code"),
        WORKBENCH_CONFIG.get("class_code"),
        SCRIPT_CONFIG.get("class_code"),
        SCRIPT_CONFIG.get("data_prefix"),
        SCRIPT_CONFIG.get("cohort_code"),
        WORKBENCH_CONFIG.get("cohort_code"),
    ):
        text = str(value or "").strip()
        if text and text.lower() != "demo":
            return text
    raise RuntimeError(
        "缺少有效的接龙群搜索班期标识：当前配置仍是 demo 或为空。"
        "请先在配置面板重新生成老师配置，确保 profile.data_prefix 为真实班期，例如 0807。"
    )


def solitaire_roster_json() -> Path:
    roster_path = DATA / "new-class-student-list.json"
    if roster_path.exists():
        return roster_path
    return data_path("students_json", SCRIPT_CONFIG)


def refresh_solitaire_roster() -> None:
    fetcher = DATA / "fetch-new-class-student-list.mjs"
    if not fetcher.exists():
        return
    try:
        run(["node", str(fetcher)])
    except subprocess.CalledProcessError as error:
        print(
            "Warning: 刷新 CRM 学员名单缓存失败，继续使用现有缓存；"
            f"如果接龙全部无法匹配，请先检查 class_pool_id 和老师配置。{error}"
        )


def solitaire_specs() -> tuple[tuple[str, str, int], ...]:
    labels = [label for _, label in class_mappings(SCRIPT_CONFIG)]

    def minimum(keyword: str) -> int:
        return sum(1 for label in labels if keyword in label)

    specs = tuple(
        (schedule, keyword, count)
        for schedule, keyword, count in (
            ("friday", "周五", minimum("周五")),
            ("saturday", "周六", minimum("周六")),
        )
        if count > 0
    )
    if not specs:
        raise RuntimeError("配置中没有可更新接龙的周五/周六班级，请先生成或检查老师配置。")
    return specs


def update_solitaire(context: WeekContext) -> None:
    since = f"{context.start.isoformat()}T00:00:00+08:00"
    until = f"{(context.end + timedelta(days=1)).isoformat()}T00:00:00+08:00"
    specs = solitaire_specs()
    class_code_filter = solitaire_class_code()
    refresh_solitaire_roster()
    sources = [solitaire_json(context, schedule) for schedule, _, _ in specs]
    roster_json = solitaire_roster_json()
    run_parallel(
        [
            [
                "node",
                str(SCRIPTS / "fetch_group_solitaire_from_crm.mjs"),
                f"--port={PORT}",
                f"--class-code={class_code_filter}",
                f"--group-keyword={keyword}",
                f"--since={since}",
                f"--until={until}",
                f"--out-dir={output.parent}",
                f"--roster={roster_json}",
            ]
            for (_, keyword, _), output in zip(specs, sources)
        ]
    )
    valid_sources: list[Path] = []
    skipped: list[str] = []
    for schedule, keyword, minimum_group_count in specs:
        output = solitaire_json(context, schedule)
        payload = json.loads(output.read_text(encoding="utf-8"))
        group_count = len(payload.get("groups") or [])
        if group_count == 0:
            skipped.append(keyword)
            print(
                f"Warning: W{context.week} {keyword}接龙群找到 0 个，"
                "本次跳过该时段；可能是对应班级还未发布接龙，稍后可单独重跑。"
            )
            continue
        if group_count < minimum_group_count:
            print(
                f"Warning: W{context.week} {keyword}接龙群只找到 {group_count} 个，"
                f"低于配置班级数 {minimum_group_count} 个；本次仍写入已抓到的数据。"
            )
        valid_sources.append(output)
    if not valid_sources:
        skipped_text = "、".join(skipped) if skipped else "全部时段"
        raise RuntimeError(f"W{context.week} 没有找到任何接龙群，已停止，未写入钉钉。跳过：{skipped_text}")
    ensure_week_columns(context)
    run(
        [
            "py",
            "-3.10",
            str(SCRIPTS / "update_weekly_solitaire.py"),
            "--week",
            str(context.week),
            *[
                argument
                for source in valid_sources
                for argument in ("--solitaire", str(source))
            ],
        ]
    )


def sync_previous_feedback_sends(context: WeekContext, strict: bool = True) -> None:
    previous_results: list[tuple[int, Path]] = []
    for path in DATA.glob(f"{PREFIX}-week*-feedback-send-result.json"):
        match = re.fullmatch(rf"{re.escape(PREFIX)}-week(\d+)-feedback-send-result\.json", path.name)
        if match and int(match.group(1)) <= context.week:
            previous_results.append((int(match.group(1)), path))
    for previous_week, result_path in sorted(previous_results):
        command = [
            "py",
            "-3.10",
            str(SCRIPTS / "sync_feedback_send_status.py"),
            "--week",
            str(previous_week),
            "--result",
            str(result_path),
        ]
        try:
            run(command)
        except subprocess.CalledProcessError:
            if strict:
                raise
            print(
                f"警告：W{previous_week} 企微反馈发送状态同步失败，"
                "本次仅更新课后学情反馈数据，已继续执行。",
                flush=True,
            )


def created_feedback_task_count(result_path: Path) -> int:
    if not result_path.exists():
        return 0
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return sum(
        1
        for item in payload.get("results") or []
        if item.get("sendable") is True and item.get("created") is True
    )


def current_feedback_sync_summary(result_path: Path) -> dict:
    if not result_path.exists():
        return {}
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    summary = payload.get("last_status_sync")
    return summary if isinstance(summary, dict) else {}


def sync_current_feedback_after_send(context: WeekContext, result_path: Path) -> None:
    created_count = created_feedback_task_count(result_path)
    if created_count <= 0:
        print("No created WeCom feedback tasks found; skip send-status sync.", flush=True)
        return

    command = [
        "py",
        "-3.10",
        str(SCRIPTS / "sync_feedback_send_status.py"),
        "--week",
        str(context.week),
        "--result",
        str(result_path),
        "--created-only",
    ]
    run(command)
    summary = current_feedback_sync_summary(result_path)
    print(
        f"W{context.week} feedback tasks created; DingTalk marked immediately: "
        f"{json.dumps(summary, ensure_ascii=False)}",
        flush=True,
    )


def update_feedback(
    context: WeekContext,
    strict_send_sync: bool = True,
    sync_previous_sends: bool = True,
) -> list[int]:
    if sync_previous_sends:
        sync_previous_feedback_sends(context, strict=strict_send_sync)
    course_files = feedback_course_files(context)
    for course_num, course_file in zip(
        (context.first_course, context.second_course),
        course_files,
    ):
        run(
            [
                "node",
                str(SCRIPTS / "fetch_course_detail_from_crm.mjs"),
                "--port",
                str(PORT),
                "--course-num",
                str(course_num),
                "--course-id",
                "0",
                "--class-file",
                str(data_path("completion_classes_csv", SCRIPT_CONFIG)),
                "--out-json",
                str(course_file),
            ]
        )
    course_ids: list[int] = []
    for path in course_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        course_id = int(payload.get("courseId") or 0)
        if course_id <= 0 or int(payload.get("detailCount") or 0) <= 0:
            raise RuntimeError(f"无法确认 {path.name} 的真实课程 ID；已停止写入。")
        course_ids.append(course_id)
    run(
        [
            "py",
            "-3.10",
            str(SCRIPTS / "create_post_class_feedback_sheet.py"),
            "--week",
            str(context.week),
            "--course-files",
            *[str(path) for path in course_files],
            "--output",
            str(feedback_csv(context)),
        ]
    )
    return course_ids


def send_finished_feedback(context: WeekContext) -> None:
    course_ids = update_feedback(
        context,
        strict_send_sync=False,
        sync_previous_sends=False,
    )
    course_files = feedback_course_files(context)
    pending_csv = DATA / f"{PREFIX}-week{context.week}-pending-personalized-feedback.csv"
    run(
        [
            "py",
            "-3.10",
            str(SCRIPTS / "generate_week1_personalized_feedback.py"),
            "--pending-only",
            "--include-missing-week-test",
            "--finished-only",
            "--week",
            str(context.week),
            "--input",
            str(feedback_csv(context)),
            "--course-files",
            *[str(path) for path in course_files],
            "--output",
            str(pending_csv),
        ]
    )
    with pending_csv.open("r", encoding="utf-8-sig", newline="") as source:
        target_count = max(0, sum(1 for _ in csv.DictReader(source)))
    if target_count == 0:
        print("没有符合条件且尚未反馈的已完课学员，本次不创建企微任务。")
        return
    result_path = DATA / f"{PREFIX}-week{context.week}-feedback-send-result.json"
    send_command = [
        "py",
        "-3.10",
        str(SCRIPTS / "send_week1_personalized_feedback.py"),
        "--input",
        str(pending_csv),
        "--week",
        str(context.week),
        "--course-id",
        str(course_ids[-1]),
        "--unlock-course-ids",
        *[str(value) for value in course_ids],
        "--result",
        str(result_path),
    ]
    run(send_command)
    run([*send_command, "--execute"])
    sync_current_feedback_after_send(context, result_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=(
            "completion",
            "live",
            "completion-and-live",
            "solitaire",
            "feedback",
            "feedback-send",
        ),
    )
    parser.add_argument(
        "--week",
        type=int,
        help="Override the calculated latest week for testing or recovery.",
    )
    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--skip-makeup",
        action="store_true",
        help="Skip syncing/formatting the makeup sheet after completion writes.",
    )
    args = parser.parse_args()
    context = context_for(week=args.week)
    if False and args.operation == "feedback-send":
        current_context = context_for()
        if context.week != current_context.week:
            raise RuntimeError(
                f"发送反馈只允许发送当周反馈：当前为 W{current_context.week}，"
                f"本次请求为 W{context.week}，已停止创建企微任务。"
            )
    if (
        args.operation
        in {"completion", "live", "completion-and-live", "feedback", "feedback-send"}
        and context.start > date.today()
    ):
        raise RuntimeError(
            f"W{context.week} 尚未开始（开始日期 {context.start.isoformat()}），"
            f"已停止 {args.operation}，避免把未来课程误记为未到课。"
        )
    persist_context(context)
    require_logged_in_crm()
    combined = args.operation == "completion-and-live"
    if args.fetch_only and args.operation not in {"completion", "live"}:
        raise RuntimeError("--fetch-only 只允许用于 completion 或 live 内部抓取")
    if combined:
        update_completion_and_live(context, sync_makeup=not args.skip_makeup)
    elif args.operation == "completion":
        fetch_completion(context)
        if not args.fetch_only:
            write_completion(context, sync_makeup=not args.skip_makeup)
    elif args.operation == "live":
        fetch_live(context)
        if not args.fetch_only:
            write_live(context)
    if args.operation == "solitaire":
        update_solitaire(context)
    if args.operation == "feedback":
        update_feedback(context, strict_send_sync=False)
    if args.operation == "feedback-send":
        send_finished_feedback(context)
    print(
        json.dumps(
            {
                "operation": args.operation,
                "week": context.week,
                "courses": [context.first_course, context.second_course],
                "success": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"流程已停止：{error}", file=sys.stderr)
        raise
