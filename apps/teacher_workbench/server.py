#!/usr/bin/env python3
"""Local graphical control panel for the CodeMao teacher-service workflows."""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen


WORKSPACE = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
SCRIPTS_DIR = WORKSPACE / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
from teacher_workbench_config import (  # noqa: E402
    DEFAULT_FEEDBACK_RULES,
    DEFAULT_PROFILE,
    class_match_prefixes,
    data_path,
    data_prefix,
    learning_sheet_target,
    script_config,
)

CONFIG_PATH = WORKSPACE / "data" / "teacher-workbench-config.json"
SCHEDULES_PATH = WORKSPACE / "data" / "workbench-schedules.json"
PROBLEM_LOG_PATH = WORKSPACE / "docs" / "工作台持续改进记录.md"
PYTHON = ["py", "-3.10"]
WORKBENCH = WORKSPACE / "scripts" / "codemao_workbench.py"
THREAD_WORKFLOW = WORKSPACE / "scripts" / "run_0724_thread_workflow.py"
INVITE_FOLLOWUP = WORKSPACE / "scripts" / "update_weekly_invite_followup.py"
INVITE_WITH_SOLITAIRE = WORKSPACE / "scripts" / "update_invite_followup_with_solitaire.py"
CLASS_TIME_SYNC = WORKSPACE / "scripts" / "sync_student_class_times.py"
CANCEL_FEEDBACK_SEND = WORKSPACE / "scripts" / "cancel_feedback_group_send.py"
CRM_NETWORK_LISTENER = WORKSPACE / "scripts" / "listen_crm_network_all_tabs.mjs"
PROFILE_DISCOVER = WORKSPACE / "scripts" / "discover_from_capture.py"
MAX_LOG_LINES = 1500
DEFAULT_CONFIG = {
    "dashboard_title": "教师工作台",
    "cohort_code": "0724",
    "brand_subtitle": "THREAD WORKFLOW",
    "cohort_start": "2026-07-23",
    "week_length_days": 7,
    "week_active_days": 5,
    "manual_opened_week": 2,
    "chrome_debug_port": 9223,
    "crm_url": "https://codecamp-crm.codemao.cn/layout/step/index",
    "theme": {"primary": "#73AE52", "accent": "#FBF1D7"},
    "invite": {"friday_prefix": "周五", "saturday_prefix": "周六", "workers": 6},
    "feedback_rules": DEFAULT_FEEDBACK_RULES,
    "profile": DEFAULT_PROFILE,
}
CHROME_PROFILE = WORKSPACE / ".chrome-debug-profile"


@dataclass(frozen=True)
class Task:
    task_id: str
    title: str
    description: str
    group: str
    commands: tuple[tuple[str, ...], ...]
    confirm: bool = False
    confirm_text: str = ""
    surface: str = "main"
    week_selectable: bool = False


TASKS = {
    task.task_id: task
    for task in (
        Task(
            "sync_student_class_times",
            "核对并更新学生时间段",
            "刷新最新 CRM 班级归属，删除退费中学员，按学生ID更新时间段，并按周五晚、周六午、周六晚排序。",
            "学员名单维护",
            (tuple([*PYTHON, str(CLASS_TIME_SYNC), "--apply"]),),
            True,
            "系统会先刷新 CRM 学员名单和退费名单；退费中的学员会从学情表删除，并从本地学员名单缓存中剔除，因此工作台总学员数会同步更新。其余学员按学生ID核对“上课时间”，更新后整行按周五晚、周六午、周六晚排序。除删除退费学员行、更新时间段、排序和恢复复选框格式外，不会修改请假、反馈、接龙、完课、直播或其他人工字段。",
        ),
        Task(
            "completion_w1",
            "更新完课数据",
            "按上方勾选周次更新每周两节课的完课状态；随后同步并格式化补课表。",
            "会话确认的更新操作",
            (tuple([*PYTHON, str(THREAD_WORKFLOW), "completion"]),),
            True,
            "系统会按您勾选的周次依次更新到课/完课情况并同步补课表；其他周、补课时间、电话跟进和反馈标记都会保留。",
            "main",
            True,
        ),
        Task(
            "live_w1",
            "更新直播数据",
            "按上方勾选周次读取直播与回放参与情况，只更新对应周直播参与列。",
            "会话确认的更新操作",
            (tuple([*PYTHON, str(THREAD_WORKFLOW), "live"]),),
            True,
            "只更新您勾选周次的直播参与情况；直播和回放都计为参与，不修改其他周、完课或人工字段。",
            "main",
            True,
        ),
        Task(
            "completion_and_live_w1",
            "同时更新完课和直播",
            "按上方勾选周次依次更新完课、直播/回放参与情况，再同步补课表。",
            "会话确认的更新操作",
            (tuple([*PYTHON, str(THREAD_WORKFLOW), "completion-and-live"]),),
            True,
            "将按周次顺序更新您勾选周次的完课和直播参与情况，并同步补课表；不会改动未勾选周、服务看板、今日待办或其他表。",
            "main",
            True,
        ),
        Task(
            "solitaire_w1",
            "更新接龙数据",
            "按上方勾选周次读取周五、周六企微群接龙，只更新对应周的接龙勾选。不会并入“同时更新完课和直播”。",
            "会话确认的更新操作",
            (tuple([*PYTHON, str(THREAD_WORKFLOW), "solitaire"]),),
            True,
            "只更新您勾选周次的接龙列；每周数据按该周起止时间独立统计，不修改完课、直播、反馈或其他人工字段。",
            "main",
            True,
        ),
        Task(
            "invite_followup_friday",
            "更新周五邀约跟进",
            "先同步所选周次的周五接龙，再更新周五班次的邀约跟进。",
            "邀约跟进",
            (
                tuple(
                    [
                        *PYTHON,
                        str(INVITE_WITH_SOLITAIRE),
                        "--class-prefix",
                        "周五",
                        "--workers",
                        "6",
                    ]
                ),
            ),
            True,
            "系统会先抓取所选周次的周五企微接龙并同步到学情表 W 周接龙列，再更新统一「邀约跟进」表中对应 W 周 + 周五的跟进数据；请假以学情表标记为准，已接龙学员不会进入跟进名单，其它周次和周六数据会保留。",
            "main",
            True,
        ),
        Task(
            "invite_followup_saturday",
            "更新周六邀约跟进",
            "先同步所选周次的周六接龙，再更新周六班次的邀约跟进。",
            "邀约跟进",
            (
                tuple(
                    [
                        *PYTHON,
                        str(INVITE_WITH_SOLITAIRE),
                        "--class-prefix",
                        "周六",
                        "--workers",
                        "6",
                    ]
                ),
            ),
            True,
            "系统会先抓取所选周次的周六企微接龙并同步到学情表 W 周接龙列，再更新统一「邀约跟进」表中对应 W 周 + 周六的跟进数据；请假以学情表标记为准，已接龙学员不会进入跟进名单，其它周次和周五数据会保留。",
            "main",
            True,
        ),
        Task(
            "post_class_feedback_w1",
            "更新课后学情反馈",
            "先同步企微最终发送结果，再读取勾选周次的课中习题、周测和笔记状态，更新对应周课后学情反馈表，并在表内标记周次/课程范围。",
            "会话确认的更新操作",
            (tuple([*PYTHON, str(THREAD_WORKFLOW), "feedback"]),),
            True,
            "系统会先核对上一次企微反馈记录，仅把最终发送成功的学生登记到对应周的“是否已反馈”；取消、待确认或失败的不登记。随后按顺序更新您勾选周次的学情数据，统一写入「课后学情反馈」总表；每次只替换当前周 rows，保留其它周，表内用“周次”和“课程范围”区分，不同周之间不会继承反馈状态。",
            "main",
            True,
        ),
        Task(
            "send_finished_feedback_w1",
            "发送已完课学员课后反馈",
            "只处理系统当前周：先更新当周学情反馈表，再为当周尚未反馈的已完课学员创建企微反馈任务。",
            "已完课名单操作",
            (tuple([*PYTHON, str(THREAD_WORKFLOW), "feedback-send"]),),
            True,
            "系统只会发送当前周反馈，不会补发历史周或其它周。运行时会先核对历史企微反馈记录，把最终发送成功的学生回写到对应周钉钉表格；取消、待确认或失败的不登记。随后刷新当前周学情数据，只筛选当前周两课均已完课且“是否已反馈”未勾选的学员。话术仅在周测全对时写“周测100%正确”，课中习题正确率严格高于80%时才会提及；其他情况不展示对应成绩。系统会先验证全部企微映射，再创建反馈任务，企微端可能仍需最终确认。",
            "detail",
        ),
        Task(
            "cancel_feedback_send",
            "取消群发",
            "按上方勾选周次读取对应反馈发送结果，取消已创建的企微群发任务。",
            "已完课名单操作",
            (
                tuple(
                    [
                        *PYTHON,
                        str(CANCEL_FEEDBACK_SEND),
                        "--execute",
                        "--continue-on-error",
                    ]
                ),
            ),
            True,
            "系统会读取所选周次的反馈发送结果，只取消其中已经创建的企微群发任务；如果企微端已经最终发送或 CRM 不允许取消，可能只能记录失败。不会删除钉钉学情数据，也不会修改反馈话术。",
            "detail",
            True,
        ),
    )
}


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_job_id: str | None = None

    def create(self, task: Task, weeks: list[int] | None = None) -> dict[str, Any]:
        with self._lock:
            if self._active_job_id:
                active = self._jobs.get(self._active_job_id)
                if active and active["status"] == "running":
                    raise RuntimeError(f"已有任务正在运行：{active['title']}")
            job_id = uuid.uuid4().hex[:12]
            selected_weeks = weeks or []
            week_suffix = (
                " · " + "、".join(f"W{week}" for week in selected_weeks)
                if selected_weeks
                else ""
            )
            job = {
                "id": job_id,
                "task_id": task.task_id,
                "title": task.title + week_suffix,
                "weeks": selected_weeks,
                "status": "running",
                "started_at": now_text(),
                "finished_at": None,
                "exit_code": None,
                "logs": deque(maxlen=MAX_LOG_LINES),
            }
            self._jobs[job_id] = job
            self._active_job_id = job_id
            return job

    def append(self, job_id: str, line: str) -> None:
        with self._lock:
            self._jobs[job_id]["logs"].append(line.rstrip())

    def finish(self, job_id: str, exit_code: int) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["exit_code"] = exit_code
            job["status"] = "success" if exit_code == 0 else "failed"
            job["finished_at"] = now_text()
            if self._active_job_id == job_id:
                self._active_job_id = None

    def public(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return serialize_job(job) if job else None

    def latest(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())[-10:]
            return [serialize_job(job) for job in reversed(jobs)]


JOBS = JobStore()
PROFILE_CAPTURE_LOCK = threading.Lock()
PROFILE_CAPTURE: dict[str, Any] = {
    "process": None,
    "log_handle": None,
    "data_prefix": "",
    "capture_path": None,
    "log_path": None,
    "started_at": None,
}
SCHEDULE_LOCK = threading.Lock()
SCHEDULER_STARTED = False
WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return deep_merge({}, DEFAULT_CONFIG)
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deep_merge({}, DEFAULT_CONFIG)
    if not isinstance(payload, dict):
        return deep_merge({}, DEFAULT_CONFIG)
    return normalize_config(deep_merge(DEFAULT_CONFIG, payload))


def normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = deep_merge(DEFAULT_CONFIG, config)
    normalized["dashboard_title"] = str(normalized.get("dashboard_title") or "教师工作台").strip()
    normalized["cohort_code"] = str(normalized.get("cohort_code") or "0724").strip()
    normalized["brand_subtitle"] = str(normalized.get("brand_subtitle") or "").strip()
    normalized["cohort_start"] = parse_iso_date_text(str(normalized.get("cohort_start") or "")).isoformat()
    normalized["week_length_days"] = clamp_int(normalized.get("week_length_days"), 1, 14, 7)
    normalized["week_active_days"] = clamp_int(
        normalized.get("week_active_days"),
        1,
        int(normalized["week_length_days"]),
        5,
    )
    normalized["manual_opened_week"] = clamp_int(normalized.get("manual_opened_week"), 1, 99, 1)
    normalized["chrome_debug_port"] = clamp_int(normalized.get("chrome_debug_port"), 1, 65535, 9223)
    normalized["crm_url"] = str(normalized.get("crm_url") or DEFAULT_CONFIG["crm_url"]).strip()
    theme = normalized.setdefault("theme", {})
    theme["primary"] = normalize_hex_color(theme.get("primary"), "#73AE52")
    theme["accent"] = normalize_hex_color(theme.get("accent"), "#FBF1D7")
    invite = normalized.setdefault("invite", {})
    invite["friday_prefix"] = str(invite.get("friday_prefix") or "周五").strip()
    invite["saturday_prefix"] = str(invite.get("saturday_prefix") or "周六").strip()
    invite["workers"] = clamp_int(invite.get("workers"), 1, 12, 6)
    normalized["feedback_rules"] = normalize_feedback_rules(normalized.get("feedback_rules"))
    normalized["profile"] = normalize_profile(normalized.get("profile"))
    return normalized


def normalize_feedback_rules(value: Any) -> dict[str, Any]:
    rules = deep_merge(DEFAULT_FEEDBACK_RULES, value if isinstance(value, dict) else {})
    regular = rules.setdefault("regular_exercise", {})
    regular["enabled"] = bool(regular.get("enabled", True))
    regular["label"] = str(regular.get("label") or "课中习题").strip() or "课中习题"
    regular["mention_threshold"] = clamp_int(regular.get("mention_threshold"), 0, 100, 80)
    regular["threshold_operator"] = ">=" if str(regular.get("threshold_operator")) == ">=" else ">"
    week_test = rules.setdefault("week_test", {})
    week_test["enabled"] = bool(week_test.get("enabled", True))
    week_test["mention_only_full_score"] = bool(week_test.get("mention_only_full_score", True))
    week_test["full_score_text"] = str(week_test.get("full_score_text") or "周测100%正确").strip()
    week_test["remind_if_missing"] = bool(week_test.get("remind_if_missing", True))
    notes = rules.setdefault("notes", {})
    notes["enabled"] = bool(notes.get("enabled", True))
    notes["mention_if_submitted"] = bool(notes.get("mention_if_submitted", True))
    rating = rules.setdefault("rating", {})
    rating["enabled"] = bool(rating.get("enabled", True))
    rating["base"] = str(rating.get("base") or "A").strip() or "A"
    rating["excellent"] = str(rating.get("excellent") or "A+").strip() or "A+"
    rating["top"] = str(rating.get("top") or "S").strip() or "S"
    rating["base_max_combined_rate"] = clamp_int(
        rating.get("base_max_combined_rate"),
        0,
        100,
        79,
    )
    rating["excellent_min_combined_rate"] = clamp_int(
        rating.get("excellent_min_combined_rate"),
        0,
        100,
        80,
    )
    rating["excellent_requires_week_test"] = bool(rating.get("excellent_requires_week_test", True))
    rating["top_min_combined_rate"] = clamp_int(
        rating.get("top_min_combined_rate"),
        0,
        100,
        95,
    )
    rating["top_requires_week_test_full_score"] = bool(
        rating.get("top_requires_week_test_full_score", True)
    )
    rating["line_template"] = str(
        rating.get("line_template") or "本周综合评级：{grade}"
    ).strip()
    contact = rules.setdefault("contact", {})
    contact["enabled"] = bool(contact.get("enabled", True))
    contact["text"] = str(contact.get("text") or "有什么问题您随时联系我哈～").strip()
    keywords = contact.get("dedupe_keywords")
    if isinstance(keywords, str):
        keywords = [item.strip() for item in re.split(r"[\n,，]+", keywords) if item.strip()]
    contact["dedupe_keywords"] = (
        [str(item).strip() for item in keywords if str(item).strip()]
        if isinstance(keywords, list)
        else DEFAULT_FEEDBACK_RULES["contact"]["dedupe_keywords"]
    )
    templates = rules.setdefault("templates", {})
    for key, fallback in DEFAULT_FEEDBACK_RULES["templates"].items():
        value = templates.get(key)
        if isinstance(value, str):
            values = [line.strip() for line in value.splitlines() if line.strip()]
        elif isinstance(value, list):
            values = [str(line).strip() for line in value if str(line).strip()]
        else:
            values = []
        templates[key] = values or list(fallback)
    return rules


def normalize_profile(value: Any) -> dict[str, Any]:
    profile = deep_merge(DEFAULT_PROFILE, value if isinstance(value, dict) else {})
    profile["data_prefix"] = str(profile.get("data_prefix") or "0724").strip() or "0724"
    dingtalk = profile.setdefault("dingtalk", {})
    for key, fallback in DEFAULT_PROFILE["dingtalk"].items():
        dingtalk[key] = str(dingtalk.get(key) or fallback).strip()
    files = profile.setdefault("files", {})
    for key, fallback in DEFAULT_PROFILE["files"].items():
        files[key] = str(files.get(key) or fallback).strip()
    rows = profile.get("classes") if isinstance(profile.get("classes"), list) else []
    classes: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            class_id = int(row.get("class_id"))
        except (TypeError, ValueError):
            continue
        label = str(row.get("label") or "").strip()
        match_prefix = str(row.get("match_prefix") or label).strip()
        if class_id and label and match_prefix:
            classes.append(
                {
                    "class_id": class_id,
                    "label": label,
                    "match_prefix": match_prefix,
                }
            )
    profile["classes"] = classes or DEFAULT_PROFILE["classes"]
    return profile


def save_config(payload: dict[str, Any]) -> dict[str, Any]:
    config = normalize_config(deep_merge(load_config(), payload))
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return config


def parse_iso_date_text(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return date.fromisoformat(str(DEFAULT_CONFIG["cohort_start"]))


def clamp_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def normalize_hex_color(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if len(text) == 7 and text.startswith("#") and all(ch in "0123456789abcdefABCDEF" for ch in text[1:]):
        return text.upper()
    return fallback


def config_date(config: dict[str, Any], key: str) -> date:
    return parse_iso_date_text(str(config.get(key) or DEFAULT_CONFIG[key]))


def config_port() -> int:
    return int(load_config()["chrome_debug_port"])


def selected_week_commands(
    task: Task,
    raw_weeks: Any,
) -> tuple[list[int], tuple[tuple[str, ...], ...]]:
    if not task.week_selectable:
        return [], task.commands
    config = load_config()
    current_week = selectable_week_number(config=config)
    if raw_weeks is None:
        weeks = [current_week]
    elif not isinstance(raw_weeks, list):
        raise ValueError("周次必须是多选列表")
    else:
        try:
            weeks = sorted(
                {
                    int(value)
                    for value in raw_weeks
                    if not isinstance(value, bool)
                }
            )
        except (TypeError, ValueError) as error:
            raise ValueError("周次只能填写数字") from error
    if not weeks:
        raise ValueError("请至少选择一个更新周次")
    invalid = [week for week in weeks if week < 1 or week > current_week]
    if invalid:
        raise ValueError(
            f"当前可更新 W1-W{current_week}，无效周次："
            + "、".join(f"W{week}" for week in invalid)
        )
    commands = tuple(
        tuple([*configured_task_command(task, command, config), "--week", str(week)])
        for week in weeks
        for command in task.commands
    )
    return weeks, commands


def configured_task_command(
    task: Task,
    command: tuple[str, ...],
    config: dict[str, Any],
) -> tuple[str, ...]:
    if task.task_id == "invite_followup_friday":
        return replace_option(
            replace_option(
                replace_option(command, "--class-prefix", config["invite"]["friday_prefix"]),
                "--workers",
                str(config["invite"]["workers"]),
            ),
            "--port",
            str(config["chrome_debug_port"]),
        )
    if task.task_id == "invite_followup_saturday":
        return replace_option(
            replace_option(
                replace_option(command, "--class-prefix", config["invite"]["saturday_prefix"]),
                "--workers",
                str(config["invite"]["workers"]),
            ),
            "--port",
            str(config["chrome_debug_port"]),
        )
    return command


def replace_option(command: tuple[str, ...], option: str, value: str) -> tuple[str, ...]:
    items = list(command)
    if option in items:
        index = items.index(option)
        if index + 1 < len(items):
            items[index + 1] = value
            return tuple(items)
    return tuple([*items, option, value])


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def serialize_job(job: dict[str, Any]) -> dict[str, Any]:
    payload = dict(job)
    payload["logs"] = list(job["logs"])
    return payload


def normalize_schedule(payload: dict[str, Any], existing_id: str | None = None) -> dict[str, Any]:
    schedule_id = str(payload.get("id") or existing_id or uuid.uuid4().hex[:10])
    task_id = str(payload.get("task_id") or "").strip()
    if task_id not in TASKS:
        raise ValueError("定时任务选择的操作不存在")
    raw_weekdays = payload.get("weekdays")
    if not isinstance(raw_weekdays, list):
        raise ValueError("请选择每周几执行")
    weekdays = sorted(
        {
            int(value)
            for value in raw_weekdays
            if not isinstance(value, bool) and 0 <= int(value) <= 6
        }
    )
    if not weekdays:
        raise ValueError("请选择每周几执行")
    run_time = str(payload.get("time") or "").strip()
    if not re.fullmatch(r"\d{2}:\d{2}", run_time):
        raise ValueError("执行时间格式应为 HH:MM")
    hour, minute = map(int, run_time.split(":", 1))
    if hour > 23 or minute > 59:
        raise ValueError("执行时间不合法")
    week_mode = str(payload.get("week_mode") or "current").strip()
    if week_mode not in {"current", "custom"}:
        week_mode = "current"
    weeks: list[int] = []
    if week_mode == "custom":
        raw_weeks = payload.get("weeks") if isinstance(payload.get("weeks"), list) else []
        weeks = sorted(
            {
                int(value)
                for value in raw_weeks
                if not isinstance(value, bool) and int(value) > 0
            }
        )
        if not weeks:
            raise ValueError("自定义周次至少填写一个 W 周")
    return {
        "id": schedule_id,
        "name": str(payload.get("name") or TASKS[task_id].title).strip() or TASKS[task_id].title,
        "enabled": bool(payload.get("enabled", True)),
        "task_id": task_id,
        "weekdays": weekdays,
        "time": run_time,
        "week_mode": week_mode,
        "weeks": weeks,
        "created_at": str(payload.get("created_at") or now_text()),
        "updated_at": now_text(),
        "last_run_key": str(payload.get("last_run_key") or ""),
        "last_run_at": str(payload.get("last_run_at") or ""),
        "last_status": str(payload.get("last_status") or ""),
        "last_job_id": str(payload.get("last_job_id") or ""),
    }


def load_schedules() -> list[dict[str, Any]]:
    if not SCHEDULES_PATH.exists():
        return []
    try:
        payload = json.loads(SCHEDULES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = payload.get("schedules") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    schedules: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            schedules.append(normalize_schedule(item, str(item.get("id") or "")))
        except Exception:
            continue
    return schedules


def save_schedules(schedules: list[dict[str, Any]]) -> None:
    SCHEDULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULES_PATH.write_text(
        json.dumps({"schedules": schedules}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def public_schedules() -> dict[str, Any]:
    schedules = load_schedules()
    return {
        "schedules": schedules,
        "weekday_labels": WEEKDAY_LABELS,
        "tasks": [
            task_payload(task)
            for task in sorted(TASKS.values(), key=lambda item: (item.group, item.title))
        ],
    }


def create_scheduled_job(schedule: dict[str, Any]) -> str:
    task = TASKS.get(str(schedule.get("task_id") or ""))
    if task is None:
        raise RuntimeError("定时任务对应的操作不存在")
    raw_weeks: Any = None
    if task.week_selectable and schedule.get("week_mode") == "custom":
        raw_weeks = schedule.get("weeks") or []
    weeks, commands = selected_week_commands(task, raw_weeks)
    job = JOBS.create(task, weeks)
    threading.Thread(
        target=run_job,
        args=(job["id"], task, commands),
        daemon=True,
    ).start()
    return str(job["id"])


def scheduler_loop() -> None:
    while True:
        now = datetime.now()
        due_key = now.strftime("%Y-%m-%d %H:%M")
        changed = False
        with SCHEDULE_LOCK:
            schedules = load_schedules()
            for schedule in schedules:
                if not schedule.get("enabled"):
                    continue
                if int(now.weekday()) not in set(schedule.get("weekdays") or []):
                    continue
                if str(schedule.get("time")) != now.strftime("%H:%M"):
                    continue
                if schedule.get("last_run_key") == due_key:
                    continue
                try:
                    job_id = create_scheduled_job(schedule)
                    schedule["last_status"] = "started"
                    schedule["last_job_id"] = job_id
                except RuntimeError as error:
                    schedule["last_status"] = f"skipped: {error}"
                    schedule["last_job_id"] = ""
                except Exception as error:
                    schedule["last_status"] = f"error: {error}"
                    schedule["last_job_id"] = ""
                schedule["last_run_key"] = due_key
                schedule["last_run_at"] = now_text()
                changed = True
            if changed:
                save_schedules(schedules)
        time.sleep(20)


def start_scheduler_once() -> None:
    global SCHEDULER_STARTED
    if SCHEDULER_STARTED:
        return
    SCHEDULER_STARTED = True
    threading.Thread(target=scheduler_loop, daemon=True).start()


def csv_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return max(0, sum(1 for _ in csv.reader(source)) - 1)


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.25)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def crm_logged_in() -> bool:
    port = config_port()
    if not is_port_open(port):
        return False
    try:
        with urlopen(
            f"http://127.0.0.1:{port}/json/list",
            timeout=1.5,
        ) as response:
            tabs = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return any(
        str(tab.get("url", "")).startswith("https://codecamp-crm.codemao.cn/")
        and "/not_login" not in str(tab.get("url", ""))
        for tab in tabs
    )


def chrome_path() -> Path:
    candidates = (
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError("未在标准安装目录找到 Google Chrome。")


def open_crm_login() -> None:
    config = load_config()
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            str(chrome_path()),
            f"--remote-debugging-port={config['chrome_debug_port']}",
            f"--user-data-dir={CHROME_PROFILE}",
            config["crm_url"],
        ],
        cwd=WORKSPACE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def file_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "updated_at": None, "rows": 0}
    return {
        "exists": True,
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%m-%d %H:%M"),
        "rows": csv_row_count(path),
    }


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def active_students_from_crm(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    students_path = data_path("students_json", config)
    payload = read_json(students_path)
    items = (
        payload.get("data", {}).get("items", [])
        if isinstance(payload, dict)
        else payload
        if isinstance(payload, list)
        else []
    )
    classes_by_id = {
        int(item.get("class_id") or 0): item
        for item in config.get("classes", [])
        if isinstance(item, dict) and int(item.get("class_id") or 0)
    }
    roster: dict[str, dict[str, str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        user_id = str(item.get("userId") or item.get("user_id") or "").strip()
        if not user_id:
            continue
        class_id = int(item.get("realClassId") or item.get("classId") or 0)
        class_config = classes_by_id.get(class_id, {})
        class_label = str(class_config.get("label") or item.get("className") or "").strip()
        day = str(item.get("dayOfWeek") or "").strip()
        time_text = str(item.get("classTime") or "").strip()
        class_time = class_label or " ".join(part for part in [day, time_text] if part)
        roster[user_id] = {
            "学生ID": user_id,
            "学生姓名": str(item.get("childName") or item.get("studentName") or "").strip(),
            "上课时间": class_time,
            "班级": str(item.get("className") or class_label or "").strip(),
        }
    return roster


def percent(count: int, total: int) -> float:
    return round(100 * count / total, 1) if total else 0.0


def calculated_week(day: date | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    cohort_start = config_date(config, "cohort_start")
    week_length_days = int(config["week_length_days"])
    week_active_days = int(config["week_active_days"])
    current = day or date.today()
    week = max(1, (current - cohort_start).days // week_length_days + 1)
    start = cohort_start + timedelta(days=(week - 1) * week_length_days)
    return {
        "week": week,
        "start": start.isoformat(),
        "end": (start + timedelta(days=week_active_days - 1)).isoformat(),
        "courses": [week * 2 - 1, week * 2],
    }


def selectable_week_number(
    day: date | None = None,
    config: dict[str, Any] | None = None,
) -> int:
    config = config or load_config()
    calculated = int(calculated_week(day, config)["week"])
    return max(calculated, int(config["manual_opened_week"]))


def completion_metrics() -> tuple[list[dict[str, Any]], str | None]:
    config = script_config()
    prefix = data_prefix(config)
    roster_path = data_path("roster_csv", config)
    completion_path = WORKSPACE / "data" / f"{prefix}-completion-query-latest.json"
    refunded_path = data_path("refunded_json", config)
    confirmed_refunded_path = data_path("confirmed_refunded_json", config)
    if not completion_path.exists():
        fallback_matches = sorted((WORKSPACE / "data").glob(f"{prefix}-completion-query-*-latest.json"))
        if fallback_matches:
            completion_path = fallback_matches[-1]
    roster_rows = read_csv_dicts(roster_path)
    roster: dict[str, dict[str, str]] = {
        str(row.get("学生ID") or "").strip(): row
        for row in roster_rows
        if str(row.get("学生ID") or "").strip()
    }
    if not roster:
        roster = active_students_from_crm(config)
    completion_payload: dict[str, Any] = {}
    if completion_path.exists():
        completion_payload = json.loads(completion_path.read_text(encoding="utf-8"))
    refunded_ids: set[str] = set()
    if refunded_path.exists():
        refunded_payload = json.loads(refunded_path.read_text(encoding="utf-8"))
        refunded_items = (
            refunded_payload.get("data", {}).get("items", [])
            if isinstance(refunded_payload, dict)
            else []
        )
        refunded_ids = {
            str(item.get("userId") or item.get("user_id") or "").strip()
            for item in refunded_items
            if isinstance(item, dict)
            and str(item.get("userId") or item.get("user_id") or "").strip()
        }
    if confirmed_refunded_path.exists():
        confirmed_payload = json.loads(
            confirmed_refunded_path.read_text(encoding="utf-8")
        )
        confirmed_items = (
            confirmed_payload.get("students", [])
            if isinstance(confirmed_payload, dict)
            else []
        )
        refunded_ids.update(
            str(item.get("userId") or "").strip()
            for item in confirmed_items
            if isinstance(item, dict) and str(item.get("userId") or "").strip()
        )
    lesson_rows = completion_payload.get("detailRows") or []
    data_week = int(completion_payload.get("targetWeek") or 1)
    first_lesson_number = data_week * 2 - 1
    second_lesson_number = first_lesson_number + 1
    lessons_by_user: dict[str, dict[int, dict[int, dict[str, Any]]]] = {}
    for row in lesson_rows:
        user_id = str(row.get("userId") or "").strip()
        class_id = int(row.get("classId") or 0)
        lesson_sort = int(row.get("lessonSort") or 0)
        if (
            user_id
            and class_id
            and lesson_sort in {first_lesson_number, second_lesson_number}
        ):
            lessons_by_user.setdefault(user_id, {}).setdefault(class_id, {})[
                lesson_sort
            ] = row
    source_ids = list(roster) if roster else list(lessons_by_user)
    all_ids = [user_id for user_id in source_ids if user_id not in refunded_ids]

    groups: dict[str, list[dict[str, str]]] = {
        "all": [],
        "absent": [],
        "first_lesson_unfinished": [],
        "arrived_unfinished": [],
        "finished": [],
    }
    prefixes_by_class = class_match_prefixes(config)
    for user_id in all_ids:
        roster_row = roster.get(user_id, {})
        class_time = str(roster_row.get("上课时间") or "").strip()
        expected_class_id = 0
        for class_id, match_prefix in prefixes_by_class.items():
            if class_time.replace(" ", "").startswith(match_prefix.replace(" ", "")):
                expected_class_id = class_id
                break
        user_class_lessons = lessons_by_user.get(user_id, {})
        lessons = user_class_lessons.get(expected_class_id, {})
        if not lessons and user_class_lessons:
            lessons = max(
                user_class_lessons.values(),
                key=lambda class_lessons: (
                    sum(
                        str(item.get("status") or "") != "无数据"
                        for item in class_lessons.values()
                    ),
                    sum(
                        str(item.get("status") or "") == "已完课"
                        for item in class_lessons.values()
                    ),
                ),
            )
        first = lessons.get(first_lesson_number, {})
        second = lessons.get(second_lesson_number, {})
        name = str(
            roster_row.get("学生姓名")
            or first.get("childName")
            or second.get("childName")
            or ""
        ).strip()
        class_name = str(
            roster_row.get("班级") or first.get("className") or second.get("className") or ""
        ).strip()
        first_status = str(first.get("status") or "")
        second_status = str(second.get("status") or "")
        if first_status == "已完课" and second_status == "已完课":
            status = "已完课"
            metric_id = "finished"
        elif second_status == "已完课":
            status = "第一课未完成"
            metric_id = "first_lesson_unfinished"
        elif first_status in {"已完课", "到课未完课"}:
            status = "到课未完课"
            metric_id = "arrived_unfinished"
        else:
            status = "未到课"
            metric_id = ""
        item = {
            "id": user_id,
            "name": name,
            "class_time": class_time,
            "class_name": class_name,
            "status": status,
        }
        groups["all"].append(item)
        if metric_id:
            groups[metric_id].append(item)
        else:
            groups["absent"].append(item)

    schedule_rank = {
        prefix: len(prefixes_by_class) - index
        for index, prefix in enumerate(prefixes_by_class.values())
    }

    def detail_sort_key(item: dict[str, str]) -> tuple[int, str, str]:
        rank = next(
            (
                value
                for label, value in schedule_rank.items()
                if label in item.get("class_time", "")
            ),
            0,
        )
        return (-rank, item.get("name", ""), item.get("id", ""))

    for items in groups.values():
        items.sort(key=detail_sort_key)
    total = len(groups["all"])
    definitions = [
        ("all", f"{prefix} 学员", "当前班级全部学员"),
        ("absent", f"W{data_week}未到课学员", "本周两节课均未产生到课记录"),
        ("first_lesson_unfinished", f"W{data_week}第一课未完课学员", "本周第二课已完成，但第一课未完成"),
        ("arrived_unfinished", f"W{data_week}到课未完课学员", "本周第一课有到课记录，但第二课尚未完成"),
        ("finished", f"W{data_week}已完课学员", "本周两节课均已完成"),
    ]
    metrics = [
        {
            "id": metric_id,
            "label": label,
            "description": description,
            "count": len(groups[metric_id]),
            "percent": percent(len(groups[metric_id]), total),
            "students": groups[metric_id],
        }
        for metric_id, label, description in definitions
    ]
    return metrics, completion_payload.get("fetchedAt")


def summary() -> dict[str, Any]:
    config = load_config()
    metrics, fetched_at = completion_metrics()
    current_week_number = selectable_week_number(config=config)
    cohort_start = config_date(config, "cohort_start")
    week_length_days = int(config["week_length_days"])
    current_week = calculated_week(
        cohort_start + timedelta(days=(current_week_number - 1) * week_length_days),
        config,
    )
    chrome_open = is_port_open(int(config["chrome_debug_port"]))
    logged_in = crm_logged_in()
    return {
        "config": public_config(config),
        "checked_at": now_text(),
        "chrome_9223_open": chrome_open,
        "chrome_9223_ready": logged_in,
        "crm_logged_in": logged_in,
        "course_fetched_at": fetched_at,
        "current_week": current_week,
        "available_weeks": [
            calculated_week(cohort_start + timedelta(days=(week - 1) * week_length_days), config)
            for week in range(1, current_week_number + 1)
        ],
        "metrics": metrics,
    }


def public_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = normalize_config(config or load_config())
    return {
        "dashboard_title": config["dashboard_title"],
        "cohort_code": config["cohort_code"],
        "brand_subtitle": config["brand_subtitle"],
        "cohort_start": config["cohort_start"],
        "week_length_days": config["week_length_days"],
        "week_active_days": config["week_active_days"],
        "manual_opened_week": config["manual_opened_week"],
        "chrome_debug_port": config["chrome_debug_port"],
        "crm_url": config["crm_url"],
        "theme": config["theme"],
        "invite": config["invite"],
        "feedback_rules": config["feedback_rules"],
        "profile": config["profile"],
    }


def safe_data_prefix(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^0-9A-Za-z_.-]+", "-", text).strip(".-_")
    return text or "new-teacher"


def profile_capture_status() -> dict[str, Any]:
    with PROFILE_CAPTURE_LOCK:
        process = PROFILE_CAPTURE.get("process")
        running = bool(process and process.poll() is None)
        capture_path = PROFILE_CAPTURE.get("capture_path")
        log_path = PROFILE_CAPTURE.get("log_path")
        return {
            "running": running,
            "data_prefix": PROFILE_CAPTURE.get("data_prefix") or "",
            "capture_path": str(capture_path) if capture_path else "",
            "log_path": str(log_path) if log_path else "",
            "started_at": PROFILE_CAPTURE.get("started_at"),
            "capture_bytes": capture_path.stat().st_size if capture_path and capture_path.exists() else 0,
            "log_tail": read_tail(log_path) if log_path and log_path.exists() else "",
        }


def read_tail(path: Path, limit: int = 2400) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-limit:].decode("utf-8", errors="replace")


def close_profile_log() -> None:
    handle = PROFILE_CAPTURE.get("log_handle")
    if handle:
        try:
            handle.close()
        except OSError:
            pass
    PROFILE_CAPTURE["log_handle"] = None


def start_profile_capture(payload: dict[str, Any]) -> dict[str, Any]:
    if not CRM_NETWORK_LISTENER.exists():
        raise RuntimeError(f"Cannot find CRM listener: {CRM_NETWORK_LISTENER}")
    prefix = safe_data_prefix(payload.get("data_prefix") or load_config().get("cohort_code"))
    capture_path = WORKSPACE / "data" / f"{prefix}-crm-capture.jsonl"
    log_path = WORKSPACE / "data" / f"{prefix}-crm-capture.log"
    with PROFILE_CAPTURE_LOCK:
        process = PROFILE_CAPTURE.get("process")
        if process and process.poll() is None:
            raise RuntimeError("CRM 监听已经在运行，请先停止并生成，或先停止监听。")
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        capture_path.write_text("", encoding="utf-8")
        log_path.write_text("", encoding="utf-8")
        log_handle = log_path.open("ab")
        command = [
            "node",
            str(CRM_NETWORK_LISTENER),
            f"--port={config_port()}",
            f"--out={capture_path}",
            "--pattern=superset|class|term|course|student|teacher|lesson|dashboard|crm|api|graphql",
            "--page-pattern=codemao",
        ]
        process = subprocess.Popen(
            command,
            cwd=WORKSPACE,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        time.sleep(0.8)
        if process.poll() is not None:
            log_handle.close()
            raise RuntimeError(
                "CRM 监听启动失败。请确认目标老师 CRM 已在 Chrome 调试窗口中打开并登录。\n"
                + read_tail(log_path)
            )
        PROFILE_CAPTURE.update(
            {
                "process": process,
                "log_handle": log_handle,
                "data_prefix": prefix,
                "capture_path": capture_path,
                "log_path": log_path,
                "started_at": now_text(),
            }
        )
    return profile_capture_status()


def stop_profile_capture() -> dict[str, Any]:
    with PROFILE_CAPTURE_LOCK:
        process = PROFILE_CAPTURE.get("process")
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        close_profile_log()
    return profile_capture_status()


def generate_profile_from_capture(payload: dict[str, Any]) -> dict[str, Any]:
    status = stop_profile_capture()
    prefix = safe_data_prefix(payload.get("data_prefix") or status.get("data_prefix") or load_config().get("cohort_code"))
    capture_path = Path(status.get("capture_path") or (WORKSPACE / "data" / f"{prefix}-crm-capture.jsonl"))
    if not capture_path.exists() or capture_path.stat().st_size == 0:
        raise RuntimeError("还没有捕获到 CRM 班级数据。请先开始监听，然后在目标老师 CRM 里刷新班级看板。")
    if not PROFILE_DISCOVER.exists():
        raise RuntimeError(f"Cannot find profile generator: {PROFILE_DISCOVER}")
    command = [
        *PYTHON,
        str(PROFILE_DISCOVER),
        "--data-prefix",
        prefix,
        "--capture-jsonl",
        str(capture_path),
        "--workspace",
        str(WORKSPACE),
        "--update-config",
        str(CONFIG_PATH),
    ]
    dingtalk_url = str(payload.get("dingtalk_url") or "").strip()
    node_id = str(payload.get("node_id") or "").strip()
    learning_sheet_id = str(payload.get("learning_sheet_id") or "").strip()
    learning_sheet_name = str(payload.get("learning_sheet_name") or "").strip()
    try:
        class_pool_id = int(payload.get("class_pool_id") or 0)
    except (TypeError, ValueError):
        class_pool_id = 0
    if dingtalk_url:
        command.extend(["--dingtalk-url", dingtalk_url])
    if node_id:
        command.extend(["--node-id", node_id])
    if learning_sheet_id:
        command.extend(["--learning-sheet-id", learning_sheet_id])
    if learning_sheet_name:
        command.extend(["--learning-sheet-name", learning_sheet_name])
    if class_pool_id > 0:
        command.extend(["--class-pool-id", str(class_pool_id)])
    if payload.get("auto_learning_sheet", True):
        command.append("--auto-learning-sheet")
    result = subprocess.run(
        command,
        cwd=WORKSPACE,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        raise RuntimeError("生成 profile 失败：\n" + result.stdout[-4000:])
    config = load_config()
    profile = config.get("profile") if isinstance(config.get("profile"), dict) else {}
    dingtalk = profile.get("dingtalk") if isinstance(profile.get("dingtalk"), dict) else {}
    schema = profile.get("learning_sheet_schema") if isinstance(profile.get("learning_sheet_schema"), dict) else {}
    missing_required = schema.get("missing_required") if isinstance(schema.get("missing_required"), list) else []
    learning_sheet_id = str(dingtalk.get("learning_sheet_id") or "").strip()
    if learning_sheet_id:
        if missing_required:
            message = (
                "已生成 profile，并已探测学情表表头；但缺少关键字段："
                + "、".join(str(item) for item in missing_required)
                + "。请在配置里补充字段映射或调整表头后再运行写入任务。"
            )
        else:
            message = "已根据 CRM 捕获和钉钉表格生成 profile，已探测学情表表头，并写入看板配置。"
    else:
        message = (
            "已根据 CRM 捕获生成 profile，但当前账号没有钉钉文档权限，"
            "未能自动识别 learning_sheet_id。请让目标老师授权钉钉文档，"
            "或在配置里手动填写 learning_sheet_id 后再运行写入任务。"
        )
    return {
        "config": public_config(config),
        "profile": config.get("profile"),
        "output": result.stdout[-4000:],
        "capture": profile_capture_status(),
        "message": message,
    }


def task_payload(task: Task) -> dict[str, Any]:
    return {
        "id": task.task_id,
        "title": task.title,
        "description": task.description,
        "group": task.group,
        "confirm": task.confirm,
        "confirm_text": task.confirm_text,
        "surface": task.surface,
        "week_selectable": task.week_selectable,
    }


def markdown_code_block(text: str) -> str:
    return "```text\n" + text.replace("```", "'''") + "\n```"


def append_problem_record(
    *,
    job_id: str,
    task: Task,
    command: tuple[str, ...],
    step_index: int,
    total_steps: int,
    exit_code: int,
    logs: list[str],
) -> None:
    PROBLEM_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not PROBLEM_LOG_PATH.exists():
        PROBLEM_LOG_PATH.write_text(
            "# 工作台持续改进记录\n\n"
            "这个文件用于记录看板执行过程中遇到的问题、排查线索和后续优化点。\n",
            encoding="utf-8",
        )
    tail = "\n".join(logs[-80:]).strip() or "无日志输出"
    entry = (
        f"\n\n## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 执行异常：{task.title}\n\n"
        f"- 任务 ID：`{task.task_id}`\n"
        f"- 作业 ID：`{job_id}`\n"
        f"- 失败步骤：{step_index}/{total_steps}\n"
        f"- 退出码：{exit_code}\n"
        f"- 命令：`{' '.join(command)}`\n\n"
        "### 最近日志\n\n"
        f"{markdown_code_block(tail)}\n\n"
        "### 处理记录\n\n"
        "- 待复盘：根据上方日志定位原因，确认是否需要修复脚本、配置或数据。\n"
    )
    with PROBLEM_LOG_PATH.open("a", encoding="utf-8") as target:
        target.write(entry)


def run_job(
    job_id: str,
    task: Task,
    commands: tuple[tuple[str, ...], ...] | None = None,
) -> None:
    exit_code = 0
    JOBS.append(job_id, f"[{now_text()}] 开始：{task.title}")
    job_commands = commands or task.commands
    total_steps = len(job_commands)
    for index, command in enumerate(job_commands, start=1):
        JOBS.append(job_id, f"$ {' '.join(command)}")
        try:
            child_env = dict(os.environ)
            child_env["PYTHONIOENCODING"] = "utf-8"
            child_env["PYTHONUTF8"] = "1"
            process = subprocess.Popen(
                list(command),
                cwd=WORKSPACE,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                JOBS.append(job_id, line)
            exit_code = process.wait()
        except Exception as error:
            JOBS.append(job_id, f"启动失败：{error}")
            exit_code = 1
        if exit_code != 0:
            JOBS.append(job_id, f"第 {index} 步失败，退出码：{exit_code}")
            job_snapshot = JOBS.public(job_id) or {}
            try:
                append_problem_record(
                    job_id=job_id,
                    task=task,
                    command=command,
                    step_index=index,
                    total_steps=total_steps,
                    exit_code=exit_code,
                    logs=list(job_snapshot.get("logs") or []),
                )
            except Exception as record_error:
                JOBS.append(job_id, f"记录问题到 Markdown 失败：{record_error}")
            break
    JOBS.append(
        job_id,
        f"[{now_text()}] {'完成' if exit_code == 0 else '失败'}：{task.title}",
    )
    JOBS.finish(job_id, exit_code)


class Handler(BaseHTTPRequestHandler):
    server_version = "CodeMaoTeacherWorkbench/1.0"

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} {format_string % args}")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/tasks":
            ordered_tasks = sorted(TASKS.values(), key=lambda task: (task.group, task.title))
            self.send_json({"tasks": [task_payload(task) for task in ordered_tasks]})
            return
        if parsed.path == "/api/schedules":
            self.send_json(public_schedules())
            return
        if parsed.path == "/api/summary":
            self.send_json(summary())
            return
        if parsed.path == "/api/config":
            self.send_json({"config": public_config()})
            return
        if parsed.path == "/api/profile-capture":
            self.send_json(profile_capture_status())
            return
        if parsed.path == "/api/jobs":
            self.send_json({"jobs": JOBS.latest()})
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            job = JOBS.public(job_id)
            if not job:
                self.send_json({"error": "任务不存在"}, HTTPStatus.NOT_FOUND)
                return
            self.send_json(job)
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/open-crm-login":
            if not self.valid_local_request():
                self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
                return
            try:
                open_crm_login()
            except RuntimeError as error:
                self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self.send_json(
                {
                    "success": True,
                    "message": "已打开专用 Chrome，请完成编程猫 CRM 登录。",
                }
            )
            return
        if parsed.path.startswith("/api/profile-capture/"):
            if not self.valid_local_request():
                self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "请求内容不是有效 JSON"}, HTTPStatus.BAD_REQUEST)
                return
            if not isinstance(payload, dict):
                self.send_json({"error": "请求内容必须是对象"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                action = parsed.path.rsplit("/", 1)[-1]
                if action == "start":
                    self.send_json(start_profile_capture(payload), HTTPStatus.ACCEPTED)
                    return
                if action == "stop":
                    self.send_json(stop_profile_capture())
                    return
                if action == "generate":
                    self.send_json(generate_profile_from_capture(payload))
                    return
            except Exception as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/api/config":
            if not self.valid_local_request():
                self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "请求内容不是有效 JSON"}, HTTPStatus.BAD_REQUEST)
                return
            if not isinstance(payload, dict):
                self.send_json({"error": "配置内容必须是对象"}, HTTPStatus.BAD_REQUEST)
                return
            config = save_config(payload)
            self.send_json(
                {
                    "config": public_config(config),
                    "message": "配置已保存，之后的看板刷新和新任务会使用新配置。",
                }
            )
            return
        if parsed.path.startswith("/api/schedules"):
            if not self.valid_local_request():
                self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "请求内容不是有效 JSON"}, HTTPStatus.BAD_REQUEST)
                return
            if not isinstance(payload, dict):
                self.send_json({"error": "请求内容必须是对象"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                with SCHEDULE_LOCK:
                    schedules = load_schedules()
                    if parsed.path == "/api/schedules/delete":
                        schedule_id = str(payload.get("id") or "")
                        schedules = [item for item in schedules if item.get("id") != schedule_id]
                        save_schedules(schedules)
                        self.send_json({"success": True, **public_schedules()})
                        return
                    if parsed.path == "/api/schedules/toggle":
                        schedule_id = str(payload.get("id") or "")
                        found = False
                        for item in schedules:
                            if item.get("id") == schedule_id:
                                item["enabled"] = bool(payload.get("enabled"))
                                item["updated_at"] = now_text()
                                found = True
                                break
                        if not found:
                            raise ValueError("定时任务不存在")
                        save_schedules(schedules)
                        self.send_json({"success": True, **public_schedules()})
                        return
                    if parsed.path == "/api/schedules/reorder":
                        raw_ids = payload.get("ids") or []
                        if not isinstance(raw_ids, list):
                            raise ValueError("排序内容必须是任务 ID 列表")
                        ordered_ids = [str(item) for item in raw_ids if str(item or "").strip()]
                        schedule_by_id = {str(item.get("id") or ""): item for item in schedules}
                        ordered_schedules = [
                            schedule_by_id[schedule_id]
                            for schedule_id in ordered_ids
                            if schedule_id in schedule_by_id
                        ]
                        ordered_id_set = {str(item.get("id") or "") for item in ordered_schedules}
                        ordered_schedules.extend(
                            item for item in schedules if str(item.get("id") or "") not in ordered_id_set
                        )
                        save_schedules(ordered_schedules)
                        self.send_json({"success": True, **public_schedules()})
                        return
                    if parsed.path == "/api/schedules/run-now":
                        schedule_id = str(payload.get("id") or "")
                        schedule = next((item for item in schedules if item.get("id") == schedule_id), None)
                        if not schedule:
                            raise ValueError("定时任务不存在")
                        job_id = create_scheduled_job(schedule)
                        schedule["last_status"] = "manual_started"
                        schedule["last_job_id"] = job_id
                        schedule["last_run_at"] = now_text()
                        save_schedules(schedules)
                        self.send_json({"success": True, "job_id": job_id, **public_schedules()}, HTTPStatus.ACCEPTED)
                        return
                    if parsed.path == "/api/schedules":
                        schedule_id = str(payload.get("id") or "")
                        previous = next((item for item in schedules if item.get("id") == schedule_id), {})
                        schedule = normalize_schedule({**previous, **payload}, schedule_id or None)
                        schedules = [item for item in schedules if item.get("id") != schedule["id"]]
                        schedules.append(schedule)
                        save_schedules(schedules)
                        self.send_json({"success": True, **public_schedules()})
                        return
                self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
            except RuntimeError as error:
                self.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
            except Exception as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path != "/api/run":
            self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
            return
        if not self.valid_local_request():
            self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "请求内容不是有效 JSON"}, HTTPStatus.BAD_REQUEST)
            return
        task = TASKS.get(str(payload.get("task_id", "")))
        if not task:
            self.send_json({"error": "未知任务"}, HTTPStatus.BAD_REQUEST)
            return
        if task.confirm and payload.get("confirmed") is not True:
            self.send_json({"error": "该任务需要二次确认"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            weeks, commands = selected_week_commands(task, payload.get("weeks"))
            job = JOBS.create(task, weeks)
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        except RuntimeError as error:
            self.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
            return
        threading.Thread(
            target=run_job,
            args=(job["id"], task, commands),
            daemon=True,
        ).start()
        self.send_json({"job_id": job["id"]}, HTTPStatus.ACCEPTED)

    def valid_local_request(self) -> bool:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("application/json"):
            return False
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and origin not in {f"http://{host}", f"https://{host}"}:
            return False
        return host.startswith("127.0.0.1:") or host.startswith("localhost:")

    def serve_static(self, path_text: str) -> None:
        relative = "index.html" if path_text in {"", "/"} else path_text.lstrip("/")
        candidate = (STATIC_DIR / relative).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        body = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    start_scheduler_once()
    url = f"http://{args.host}:{args.port}"
    print(f"教师工作台已启动：{url}")
    print("按 Ctrl+C 可停止。")
    if not args.no_browser:
        threading.Thread(
            target=lambda: (time.sleep(0.6), webbrowser.open(url)),
            daemon=True,
        ).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
