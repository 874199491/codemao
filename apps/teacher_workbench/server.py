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
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


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
from week_context import (  # noqa: E402
    course_numbers_for_week,
    regular_course_index,
)

CONFIG_PATH = WORKSPACE / "data" / "teacher-workbench-config.json"
SCHEDULES_PATH = WORKSPACE / "data" / "workbench-schedules.json"
PROBLEM_LOG_PATH = WORKSPACE / "docs" / "工作台持续改进记录.md"
PYTHON = [sys.executable]
NO_CONSOLE_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WORKBENCH = WORKSPACE / "scripts" / "codemao_workbench.py"
THREAD_WORKFLOW = WORKSPACE / "scripts" / "run_0724_thread_workflow.py"
INVITE_FOLLOWUP = WORKSPACE / "scripts" / "update_weekly_invite_followup.py"
INVITE_WITH_SOLITAIRE = WORKSPACE / "scripts" / "update_invite_followup_with_solitaire.py"
CLASS_TIME_SYNC = WORKSPACE / "scripts" / "sync_student_class_times.py"
CANCEL_FEEDBACK_SEND = WORKSPACE / "scripts" / "cancel_feedback_group_send.py"
NCT_EXAM_SYNC = WORKSPACE / "scripts" / "update_nct_exam_sheet.py"
CRM_NETWORK_LISTENER = WORKSPACE / "scripts" / "listen_crm_network_all_tabs.mjs"
PROFILE_DISCOVER = WORKSPACE / "scripts" / "discover_from_capture.py"
UPDATE_WORKBENCH = WORKSPACE / "update-workbench.ps1"
CLEAN_DATA_CACHE = WORKSPACE / "scripts" / "cleanup_data_cache.py"
MONTHLY_EXAM_PREPARE = WORKSPACE / "scripts" / "prepare_monthly_exam_feedback.py"
MONTHLY_EXAM_SEND = WORKSPACE / "scripts" / "create_monthly_exam_task.py"
MONTHLY_EXAM_CANCEL = WORKSPACE / "scripts" / "cancel_monthly_exam_feedback.py"
MONTHLY_EXAM_GENERATOR = WORKSPACE / "scripts" / "run_monthly_exam_generator.py"
MONTHLY_EXAM_GEN_ALL = WORKSPACE / "scripts" / "generate_report_and_award.py"
MONTHLY_EXAM_AWARD_GEN = WORKSPACE / "scripts" / "generate_award_images.py"
MONTHLY_EXAM_DEPS = WORKSPACE / "scripts" / "ensure_monthly_exam_dependencies.py"
MONTHLY_EXAM_UNREPLIED = WORKSPACE / "scripts" / "check_unreplied_parents.py"
MONTHLY_EXAM_RUNTIME = WORKSPACE / "data" / "monthly-exam-feedback"
MAX_LOG_LINES = 1500
DEFAULT_CONFIG = {
    "dashboard_title": "教师工作台",
    "cohort_code": "0724",
    "brand_subtitle": "THREAD WORKFLOW",
    "cohort_start": "2026-07-23",
    "week_length_days": 7,
    "week_active_days": 5,
    "manual_opened_week": 2,
    "has_exam_training_lessons": False,
    "chrome_debug_port": 9223,
    "crm_url": "https://codecamp-crm.codemao.cn/layout/step/index",
    "theme": {"primary": "#73AE52", "accent": "#FBF1D7"},
    "invite": {"friday_prefix": "周五", "saturday_prefix": "周六", "workers": 6},
    "feedback_rules": DEFAULT_FEEDBACK_RULES,
    "monthly_exam_feedback": {
        # 相对路径：自动解析为 <工作区>/月考反馈助手（老师副本自带素材），不依赖具体解压路径
        "source_dir": "月考反馈助手",
        "score_file": "",
        "roster_json": "data/new-class-student-list.json",
        "templates_dir": "",
        "pdf_dir": "全班错题报告",
        "award_dir": "已生成奖状",
        "teacher_name": "",
        "send_wrong_report": True,
        "send_award": True,
        "award_threshold": 70,
        "protective_score_enabled": False,
        "templates": {band: "" for band in ("0-69", "70-79", "80-89", "90-99", "100")},
    },
    "profile": DEFAULT_PROFILE,
}
CHROME_PROFILE = WORKSPACE / ".chrome-debug-profile"
COMPLETION_METRICS_CACHE_LOCK = threading.Lock()
COMPLETION_METRICS_CACHE: dict[str, Any] = {"signature": None, "value": None}
CRM_LOGIN_CACHE_LOCK = threading.Lock()
CRM_LOGIN_CACHE: dict[str, Any] = {"port": None, "checked_at": 0.0, "value": False}


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
            "nct_exam_sync",
            "更新NCT考级",
            "更新「NCT考级」sheet，并同步学情表中的“是否有购买年卡”列。",
            "会话确认的更新操作",
            (tuple([*PYTHON, str(NCT_EXAM_SYNC)]),),
            True,
            "系统会从 CRM NCT 考级面板读取已配置班级的考级/年卡数据，写入钉钉「NCT考级」sheet；如果学情表没有“是否有购买年卡”列会自动创建，并按学生ID批量同步为复选框。不会修改完课、直播、接龙、反馈或请假字段。",
            "main",
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
            "系统只会发送当前周反馈，不会补发历史周或其它周。运行时会先刷新当前周学情数据，只筛选当前周两课均已完课且“是否已反馈”未勾选的学员。系统会先验证全部企微映射，再创建反馈任务；任务创建成功后会立即把对应学生标记为已反馈，不再等待企微最终发送状态。若后续点击取消群发，系统会把对应学生的已反馈勾选同步取消。",
            "detail",
            True,
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
                        "--all-matches",
                    ]
                ),
            ),
            True,
            "系统会读取所选周次的反馈发送结果，只取消其中已经创建的企微群发任务；同时会把课后学情反馈表中对应学生的“是否已反馈”取消勾选，并把本地发送记录标记为已取消，避免后续重新同步为已反馈。如果企微端已经最终发送或 CRM 不允许取消，会记录失败，不会修改反馈话术。",
            "detail",
            True,
        ),
        Task(
            "update_workbench",
            "一键更新工作台",
            "从配置的 GitHub 更新源拉取最新版本，保留本地配置、CRM 登录缓存、运行缓存和定时任务。",
            "系统维护",
            (
                tuple(
                    [
                        "powershell",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(UPDATE_WORKBENCH),
                        "-NoPause",
                    ]
                ),
            ),
            True,
            "系统会在当前目录内执行一键更新脚本，保留老师本地配置、CRM cookie、运行缓存和定时任务。更新完成后请重启教师工作台再继续操作。",
            "utility",
        ),
        Task(
            "cleanup_data_cache",
            "清理历史缓存",
            "清理可重新生成的旧周数据、旧家长聊天缓存、旧查询缓存和重跑备份，释放 data 目录空间。",
            "系统维护",
            (tuple([*PYTHON, str(CLEAN_DATA_CACHE), "--apply", "--keep-weeks", "4", "--keep-days", "35"]),),
            True,
            "系统会先在运行日志中展示预计清理的文件数和空间；只清理可重新生成的历史缓存。不会删除老师配置、CRM cookie、定时任务、更新源、学员基础名单，也不会改动钉钉表格或 CRM 数据。",
            "utility",
        ),
    )
}
TASK_ORDER = {
    "completion_and_live_w1": 10,
    "solitaire_w1": 20,
    "live_w1": 30,
    "completion_w1": 40,
    "post_class_feedback_w1": 50,
    "nct_exam_sync": 60,
}


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_job_id: str | None = None
        self._processes: dict[str, subprocess.Popen[str]] = {}

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
                "stop_requested": False,
                "logs": deque(maxlen=MAX_LOG_LINES),
            }
            self._jobs[job_id] = job
            self._active_job_id = job_id
            return job

    def active_job_id(self) -> str | None:
        with self._lock:
            return self._active_job_id

    def append(self, job_id: str, line: str) -> None:
        with self._lock:
            self._jobs[job_id]["logs"].append(line.rstrip())

    def register_process(self, job_id: str, process: subprocess.Popen[str]) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._processes[job_id] = process

    def unregister_process(self, job_id: str, process: subprocess.Popen[str]) -> None:
        with self._lock:
            if self._processes.get(job_id) is process:
                self._processes.pop(job_id, None)

    def stop_requested(self, job_id: str) -> bool:
        with self._lock:
            return bool(self._jobs.get(job_id, {}).get("stop_requested"))

    def request_stop(self, job_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            target_job_id = job_id or self._active_job_id
            if not target_job_id:
                raise RuntimeError("当前没有正在运行的任务")
            job = self._jobs.get(target_job_id)
            if not job or job.get("status") not in {"running", "stopping"}:
                raise RuntimeError("当前没有正在运行的任务")
            job["stop_requested"] = True
            job["status"] = "stopping"
            job["logs"].append(f"[{now_text()}] 已请求暂停执行，正在停止后台进程…")
            process = self._processes.get(target_job_id)

        if process and process.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        cwd=WORKSPACE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=NO_CONSOLE_WINDOW,
                        check=False,
                    )
                else:
                    process.terminate()
            except Exception as error:
                self.append(target_job_id, f"暂停失败：{error}")
                raise RuntimeError(f"暂停失败：{error}") from error
        return self.public(target_job_id) or {"id": target_job_id, "status": "stopping"}

    def finish(self, job_id: str, exit_code: int) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["exit_code"] = exit_code
            job["status"] = (
                "stopped"
                if job.get("stop_requested")
                else "success" if exit_code == 0 else "failed"
            )
            job["finished_at"] = now_text()
            self._processes.pop(job_id, None)
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
    normalized["has_exam_training_lessons"] = bool(
        normalized.get("has_exam_training_lessons", False)
    )
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
    normalized["monthly_exam_feedback"] = normalize_monthly_exam_feedback(
        normalized.get("monthly_exam_feedback")
    )
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
    homework_correction = rules.setdefault("homework_correction", {})
    homework_correction["enabled"] = bool(homework_correction.get("enabled", True))
    homework_correction["text"] = str(
        homework_correction.get("text")
        or "课后作业里有错题的话，建议课后再抽一点时间完成订正，把出错的地方重新过一遍。"
    ).strip()
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
    weekly_knowledge = rules.setdefault("weekly_knowledge", {})
    weekly_knowledge["enabled"] = bool(weekly_knowledge.get("enabled", True))
    weeks = weekly_knowledge.get("weeks")
    if not isinstance(weeks, dict):
        weeks = {}
    normalized_weeks: dict[str, Any] = {}
    default_weeks = DEFAULT_FEEDBACK_RULES.get("weekly_knowledge", {}).get("weeks", {})
    merged_weeks = deep_merge(default_weeks if isinstance(default_weeks, dict) else {}, weeks)
    for week, value in merged_weeks.items():
        if not isinstance(value, dict):
            continue
        week_key = str(week).strip()
        topics = value.get("topics")
        if isinstance(topics, str):
            topic_values = [item.strip() for item in re.split(r"[\n,，、]+", topics) if item.strip()]
        elif isinstance(topics, list):
            topic_values = [str(item).strip() for item in topics if str(item).strip()]
        else:
            topic_values = []
        normalized_weeks[week_key] = {
            "topics": topic_values,
            "solid": str(value.get("solid") or "").strip(),
            "minor": str(value.get("minor") or "").strip(),
            "weak": str(value.get("weak") or "").strip(),
        }
    weekly_knowledge["weeks"] = normalized_weeks
    return rules


MONTHLY_EXAM_BANDS = ("0-69", "70-79", "80-89", "90-99", "100")
MONTHLY_EXAM_BAND_RANGES = (
    (0, 69, "0-69"),
    (70, 79, "70-79"),
    (80, 89, "80-89"),
    (90, 99, "90-99"),
    (100, 100, "100"),
)


def normalize_monthly_exam_feedback(value: Any) -> dict[str, Any]:
    defaults = DEFAULT_CONFIG["monthly_exam_feedback"]
    source = value if isinstance(value, dict) else {}
    normalized = deep_merge(defaults, source)
    for key in ("source_dir", "score_file", "roster_json", "templates_dir", "pdf_dir", "award_dir", "teacher_name"):
        normalized[key] = str(normalized.get(key) or defaults[key]).strip()
    normalized["send_wrong_report"] = bool(normalized.get("send_wrong_report", True))
    normalized["send_award"] = bool(normalized.get("send_award", True))
    normalized["award_threshold"] = clamp_int(normalized.get("award_threshold"), 0, 100, 70)
    normalized["protective_score_enabled"] = bool(normalized.get("protective_score_enabled", False))
    templates = normalized.get("templates") if isinstance(normalized.get("templates"), dict) else {}
    normalized["templates"] = {band: str(templates.get(band) or "").strip() for band in MONTHLY_EXAM_BANDS}
    return normalized


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
    latest_selected_week = max(weeks)
    completion_task_ids = {"completion_w1", "completion_and_live_w1"}
    commands_list: list[tuple[str, ...]] = []
    for week in weeks:
        for command in task.commands:
            configured = [
                *configured_task_command(task, command, config),
                "--week",
                str(week),
            ]
            if (
                task.task_id in completion_task_ids
                and len(weeks) > 1
                and week != latest_selected_week
            ):
                configured.append("--skip-makeup")
            commands_list.append(tuple(configured))
    commands = tuple(commands_list)
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
            for task in sorted(TASKS.values(), key=task_sort_key)
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
    now = time.monotonic()
    with CRM_LOGIN_CACHE_LOCK:
        if CRM_LOGIN_CACHE["port"] == port and now - CRM_LOGIN_CACHE["checked_at"] < 2.0:
            return bool(CRM_LOGIN_CACHE["value"])
    value = False
    if not is_port_open(port):
        value = False
    else:
        try:
            with urlopen(
                f"http://127.0.0.1:{port}/json/list",
                timeout=1.5,
            ) as response:
                tabs = json.loads(response.read().decode("utf-8"))
            value = any(
                str(tab.get("url", "")).startswith("https://codecamp-crm.codemao.cn/")
                and "/not_login" not in str(tab.get("url", ""))
                for tab in tabs
            )
        except (OSError, ValueError, json.JSONDecodeError):
            value = False
    with CRM_LOGIN_CACHE_LOCK:
        CRM_LOGIN_CACHE.update({"port": port, "checked_at": time.monotonic(), "value": value})
    return value


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
        creationflags=NO_CONSOLE_WINDOW,
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


def class_time_sort_key(value: Any) -> tuple[int, str]:
    text = str(value or "")
    if "周五" in text:
        return (1, text)
    if "周六午" in text:
        return (2, text)
    if "周六晚" in text:
        return (3, text)
    if "周六" in text:
        return (4, text)
    return (99, text)


def calculated_week(day: date | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    cohort_start = config_date(config, "cohort_start")
    week_length_days = int(config["week_length_days"])
    week_active_days = int(config["week_active_days"])
    current = day or date.today()
    week = max(1, (current - cohort_start).days // week_length_days + 1)
    start = cohort_start + timedelta(days=(week - 1) * week_length_days)
    courses = course_numbers_for_week(
        week,
        bool(config.get("has_exam_training_lessons", False)),
    )
    return {
        "week": week,
        "start": start.isoformat(),
        "end": (start + timedelta(days=week_active_days - 1)).isoformat(),
        "courses": list(courses),
    }


def selectable_week_number(
    day: date | None = None,
    config: dict[str, Any] | None = None,
) -> int:
    config = config or load_config()
    calculated = int(calculated_week(day, config)["week"])
    return max(calculated, int(config["manual_opened_week"]))


def completion_payload_paths(config: dict[str, Any]) -> list[Path]:
    prefix = data_prefix(config)
    data_dir = WORKSPACE / "data"
    candidates = [
        data_dir / f"{prefix}-completion-query-latest.json",
        *sorted(data_dir.glob(f"{prefix}-week*-completion-query-latest.json")),
        *sorted(data_dir.glob(f"{prefix}-completion-query-*-latest.json")),
    ]
    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if candidate.exists() and resolved not in seen:
            seen.add(resolved)
            unique.append(candidate)
    return unique


def roster_and_refunds(config: dict[str, Any]) -> tuple[dict[str, dict[str, str]], set[str]]:
    roster_path = data_path("roster_csv", config)
    refunded_path = data_path("refunded_json", config)
    confirmed_refunded_path = data_path("confirmed_refunded_json", config)
    roster_rows = read_csv_dicts(roster_path)
    roster: dict[str, dict[str, str]] = {
        str(row.get("学生ID") or "").strip(): row
        for row in roster_rows
        if str(row.get("学生ID") or "").strip()
    }
    if not roster:
        roster = active_students_from_crm(config)
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
    return roster, refunded_ids


def build_completion_metrics(
    completion_payload: dict[str, Any],
    roster: dict[str, dict[str, str]],
    refunded_ids: set[str],
    config: dict[str, Any],
) -> dict[str, Any]:
    prefix = data_prefix(config)
    lesson_rows = completion_payload.get("detailRows") or []
    data_week = int(completion_payload.get("targetWeek") or 1)
    first_lesson_number, second_lesson_number = course_numbers_for_week(
        data_week,
        bool(config.get("has_exam_training_lessons", False)),
    )
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
        if not name or not class_time:
            groups.setdefault("missing_profile", []).append(
                {
                    **item,
                    "status": "信息缺失",
                }
            )

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
    anomaly_definitions = [
        ("anomaly_absent", f"W{data_week}未到课", "两节课均未产生到课记录", groups["absent"]),
        ("anomaly_arrived_unfinished", f"W{data_week}到课未完课", "第一课有到课记录，但第二课尚未完成", groups["arrived_unfinished"]),
        ("anomaly_first_lesson", f"W{data_week}第一课未完课", "第二课已完成，但第一课未完成", groups["first_lesson_unfinished"]),
        ("anomaly_missing_profile", "信息缺失", "缺少姓名或上课时间，建议先核对学员名单", groups.get("missing_profile", [])),
    ]
    anomalies = [
        {
            "id": anomaly_id,
            "label": label,
            "description": description,
            "count": len(students),
            "percent": percent(len(students), total),
            "students": students,
            "severity": "high" if anomaly_id == "anomaly_absent" else "medium",
            "source": "完课缓存与学员名单实时计算",
        }
        for anomaly_id, label, description, students in anomaly_definitions
    ]
    return {
        "week": data_week,
        "first_lesson": first_lesson_number,
        "second_lesson": second_lesson_number,
        "total": total,
        "metrics": metrics,
        "anomalies": anomalies,
        "fetched_at": completion_payload.get("fetchedAt"),
    }


def completion_metrics() -> tuple[list[dict[str, Any]], str | None, list[dict[str, Any]]]:
    config = script_config()
    payload_paths = completion_payload_paths(config)
    tracked_paths = [
        CONFIG_PATH,
        *payload_paths,
        data_path("roster_csv", config),
        data_path("refunded_json", config),
        data_path("confirmed_refunded_json", config),
    ]
    signature = tuple(
        (str(path), path.stat().st_mtime_ns, path.stat().st_size)
        for path in tracked_paths
        if path.exists()
    )
    with COMPLETION_METRICS_CACHE_LOCK:
        cached = COMPLETION_METRICS_CACHE
        if cached["signature"] == signature and cached["value"] is not None:
            return cached["value"]
    completion_payload: dict[str, Any] = {}
    if payload_paths:
        completion_payload = json.loads(payload_paths[0].read_text(encoding="utf-8"))
    roster, refunded_ids = roster_and_refunds(config)
    result = build_completion_metrics(completion_payload, roster, refunded_ids, config)
    value = (result["metrics"], result["fetched_at"], result["anomalies"])
    with COMPLETION_METRICS_CACHE_LOCK:
        COMPLETION_METRICS_CACHE.update({"signature": signature, "value": value})
    return value


def live_participation_rate(prefix: str, week: int) -> float | None:
    candidates = [
        WORKSPACE / "data" / f"{prefix}-week{week}-live-absent-latest.json",
        WORKSPACE / "data" / f"{prefix}-week{week}-live-all-latest.json",
    ]
    payload: dict[str, Any] | None = None
    boards: list[dict[str, Any]] = []
    for path in candidates:
        current = read_json(path)
        if not isinstance(current, dict):
            continue
        current_boards = current.get("boards") or []
        if not current_boards:
            seen_board_keys: set[str] = set()
            for item in current.get("rows", []) or []:
                if not isinstance(item, dict) or not isinstance(item.get("board"), dict):
                    continue
                board = item["board"]
                key = "|".join(str(value) for value in board.get("classIdList") or [])
                key = key or str(board.get("id") or board.get("boardId") or "")
                if not key or key in seen_board_keys:
                    continue
                seen_board_keys.add(key)
                current_boards.append(board)
        if current_boards:
            payload = current
            boards = current_boards
            break
    if not isinstance(payload, dict) or not boards:
        return None
    rates: list[float] = []
    for board in boards:
        if not isinstance(board, dict):
            continue
        try:
            rate = float(board.get("participatePersonRate") or board.get("livingWatchRate"))
        except (TypeError, ValueError):
            continue
        rates.append(rate)
    if not rates:
        return None
    return round(sum(rates) / len(rates), 1)


def weekly_trends() -> dict[str, Any]:
    config = script_config()
    prefix = data_prefix(config)
    roster, refunded_ids = roster_and_refunds(config)
    payloads_by_week: dict[int, tuple[Path, dict[str, Any]]] = {}
    for path in completion_payload_paths(config):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        week = int(payload.get("targetWeek") or 0)
        if week <= 0:
            continue
        previous = payloads_by_week.get(week)
        if previous is None or path.stat().st_mtime >= previous[0].stat().st_mtime:
            payloads_by_week[week] = (path, payload)

    points: list[dict[str, Any]] = []
    for week, (path, payload) in sorted(payloads_by_week.items()):
        result = build_completion_metrics(payload, roster, refunded_ids, config)
        metric_by_id = {metric["id"]: metric for metric in result["metrics"]}
        total = int(result["total"] or 0)
        students_by_metric = {
            str(metric.get("id") or ""): metric.get("students") or []
            for metric in result["metrics"]
            if str(metric.get("id") or "")
        }
        points.append(
            {
                "week": week,
                "label": f"W{week}",
                "courses": list(
                    course_numbers_for_week(
                        week,
                        bool(config.get("has_exam_training_lessons", False)),
                    )
                ),
                "total": total,
                "finished": int(metric_by_id.get("finished", {}).get("count") or 0),
                "finished_rate": float(metric_by_id.get("finished", {}).get("percent") or 0),
                "absent": int(metric_by_id.get("absent", {}).get("count") or 0),
                "absent_rate": float(metric_by_id.get("absent", {}).get("percent") or 0),
                "arrived_unfinished": int(metric_by_id.get("arrived_unfinished", {}).get("count") or 0),
                "arrived_unfinished_rate": float(metric_by_id.get("arrived_unfinished", {}).get("percent") or 0),
                "first_lesson_unfinished": int(metric_by_id.get("first_lesson_unfinished", {}).get("count") or 0),
                "first_lesson_unfinished_rate": float(metric_by_id.get("first_lesson_unfinished", {}).get("percent") or 0),
                "live_rate": live_participation_rate(prefix, week),
                "fetched_at": result["fetched_at"],
                "source": path.name,
                "students_by_metric": students_by_metric,
            }
        )

    series = [
        {"key": "finished_rate", "label": "完课率", "color": "#367a4b"},
        {"key": "absent_rate", "label": "未到课率", "color": "#bd4b45"},
        {"key": "arrived_unfinished_rate", "label": "到课未完课率", "color": "#d69b2d"},
        {"key": "first_lesson_unfinished_rate", "label": "第一课未完课率", "color": "#5c7cfa"},
        {"key": "live_rate", "label": "直播参与率", "color": "#6b8e23", "optional": True},
    ]
    return {
        "config": public_config(load_config()),
        "checked_at": now_text(),
        "points": points,
        "series": series,
        "message": (
            "已读取本地多周缓存。"
            if len(points) > 1
            else "当前只有 1 周本地完课缓存；后续更新更多周后，趋势会自动补齐。"
        ),
    }


def student_risk_snapshot() -> dict[str, Any]:
    """Build a local, read-only student risk segmentation from cached data."""
    config = script_config()
    prefix = data_prefix(config)
    roster, refunded_ids = roster_and_refunds(config)
    payloads_by_week: dict[int, tuple[Path, dict[str, Any]]] = {}
    for path in completion_payload_paths(config):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            week = int(payload.get("targetWeek") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if week <= 0:
            continue
        old = payloads_by_week.get(week)
        if old is None or path.stat().st_mtime >= old[0].stat().st_mtime:
            payloads_by_week[week] = (path, payload)

    weeks = sorted(payloads_by_week)
    current_week = weeks[-1] if weeks else selectable_week_number(config=load_config())
    recent_weeks = [week for week in weeks if week <= current_week][-4:]

    status_by_user: dict[str, dict[int, str]] = {
        user_id: {} for user_id in roster if user_id and user_id not in refunded_ids
    }
    source_by_week: dict[int, str] = {}
    for week in weeks:
        path, payload = payloads_by_week[week]
        source_by_week[week] = path.name
        result = build_completion_metrics(payload, roster, refunded_ids, config)
        for metric in result.get("metrics") or []:
            for student in metric.get("students") or []:
                user_id = str(student.get("id") or "").strip()
                if user_id in status_by_user:
                    status_by_user[user_id][week] = str(student.get("status") or "未返回")

    def build_action(level: str, reasons: list[str], status: str) -> str:
        reason_text = "；".join(reasons[:2])
        if level == "high":
            if "未到课" in status:
                return "优先确认本周学习安排，补齐回看/补课时间，再单独同步家长。"
            return "先看未完课原因，再给家长发一条短反馈，重点是把学习节奏拉回来。"
        if level == "follow":
            return "本周安排一次轻跟进，确认是否需要提醒补课或回看。"
        if level == "excellent":
            return "适合做正向反馈，可以引导拔高练习、NCT 或阶段性成长总结。"
        return f"保持观察即可；{reason_text or '当前没有明显异常'}。"

    students: list[dict[str, Any]] = []
    for user_id, roster_row in roster.items():
        if not user_id or user_id in refunded_ids:
            continue
        name = str(roster_row.get("学生姓名") or roster_row.get("孩子姓名") or "").strip()
        class_time = str(roster_row.get("上课时间") or "").strip() or "未记录"
        class_name = str(roster_row.get("班级") or "").strip()
        week_statuses = [
            {"week": week, "label": f"W{week}", "status": status_by_user.get(user_id, {}).get(week, "未返回")}
            for week in recent_weeks
        ]
        current_status = status_by_user.get(user_id, {}).get(current_week, "未返回")
        score = 0
        reasons: list[str] = []
        tags: list[str] = []

        if current_status == "未到课":
            score += 35
            reasons.append(f"W{current_week} 未到课")
            tags.append("本周未到课")
        elif current_status in {"到课未完课", "第一课未完成"}:
            score += 22
            reasons.append(f"W{current_week} {current_status}")
            tags.append("本周未完课")
        elif current_status in {"未返回", ""}:
            score += 12
            reasons.append(f"W{current_week} 缺少完课缓存")
            tags.append("数据待确认")

        recent_unfinished = [
            row for row in week_statuses
            if row["status"] in {"未到课", "到课未完课", "第一课未完成", "未返回"}
        ]
        consecutive = 0
        for row in reversed(week_statuses):
            if row["status"] in {"未到课", "到课未完课", "第一课未完成", "未返回"}:
                consecutive += 1
            else:
                break
        if consecutive >= 2:
            score += 18 + (consecutive - 2) * 8
            reasons.append(f"连续 {consecutive} 周未稳定完课")
            tags.append("连续掉队")
        elif len(recent_unfinished) >= 2:
            score += 12
            reasons.append(f"近 {len(recent_weeks)} 周有 {len(recent_unfinished)} 次异常")
            tags.append("近期波动")

        finished_recent = sum(1 for row in week_statuses if row["status"] == "已完课")
        if recent_weeks and finished_recent == len(recent_weeks) and finished_recent >= 3:
            score -= 14
            tags.append("连续完课")
        if current_status == "已完课":
            tags.append("本周已完课")

        if score >= 58:
            level = "high"
            level_label = "高风险"
        elif score >= 28:
            level = "follow"
            level_label = "需跟进"
        elif score <= -8:
            level = "excellent"
            level_label = "优秀稳定"
        else:
            level = "stable"
            level_label = "稳定观察"
        if not reasons:
            reasons.append("近期学习节奏正常")
        students.append(
            {
                "student_id": user_id,
                "student_name": name,
                "class_time": class_time,
                "class_name": class_name,
                "risk_score": max(0, score),
                "level": level,
                "level_label": level_label,
                "current_status": current_status or "未返回",
                "reasons": reasons,
                "tags": list(dict.fromkeys(tags))[:5],
                "week_statuses": week_statuses,
                "next_action": build_action(level, reasons, current_status),
            }
        )

    level_order = {"high": 0, "follow": 1, "stable": 2, "excellent": 3}
    students.sort(key=lambda row: (level_order.get(row["level"], 9), -int(row["risk_score"]), class_time_sort_key(row["class_time"]), row["student_name"], row["student_id"]))
    total = len(students)
    segments = []
    for level, label in (("high", "高风险"), ("follow", "需跟进"), ("stable", "稳定观察"), ("excellent", "优秀稳定")):
        count = sum(1 for row in students if row["level"] == level)
        segments.append({"level": level, "label": label, "count": count, "percent": percent(count, total)})
    return {
        "config": public_config(load_config()),
        "checked_at": now_text(),
        "cohort": prefix,
        "current_week": current_week,
        "recent_weeks": recent_weeks,
        "source_by_week": source_by_week,
        "total": total,
        "segments": segments,
        "students": students,
        "message": "风险分层基于本地完课缓存和学员名单计算，重点看当前周状态、连续异常和近几周波动，不会写入钉钉或 CRM。",
    }


def monthly_performance(query: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Build the 15th-to-15th performance view from cached completion data."""
    config = script_config()
    today = date.today()
    query = query or {}
    try:
        target_year = int((query.get("year") or [str(today.year)])[0])
        target_month = int((query.get("month") or [str(today.month)])[0])
        if target_month < 1 or target_month > 12:
            raise ValueError
    except (TypeError, ValueError):
        target_year, target_month = today.year, today.month
    end = date(target_year, target_month, 15)
    # A performance month covers the 16th of the previous month through
    # the 15th of the selected month, both endpoints included.
    start = date(target_year - 1, 12, 16) if target_month == 1 else date(target_year, target_month - 1, 16)
    roster, refunded_ids = roster_and_refunds(config)
    class_labels = {
        int(item.get("class_id")): str(item.get("label") or "")
        for item in (config.get("profile", {}).get("classes", []) if isinstance(config.get("profile"), dict) else [])
        if str(item.get("class_id") or "").isdigit()
    }

    latest_by_week: dict[int, tuple[Path, dict[str, Any]]] = {}
    for path in completion_payload_paths(config):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            week = int(payload.get("targetWeek") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if week <= 0:
            continue
        old = latest_by_week.get(week)
        if old is None or path.stat().st_mtime >= old[0].stat().st_mtime:
            latest_by_week[week] = (path, payload)

    selected: list[dict[str, Any]] = []
    for week, (path, payload) in sorted(latest_by_week.items()):
        raw_start = str(payload.get("weekStart") or "").strip()
        try:
            week_start = date.fromisoformat(raw_start[:10]) if raw_start else config_date(config, "cohort_start") + timedelta(days=(week - 1) * int(config["week_length_days"]))
        except ValueError:
            week_start = config_date(config, "cohort_start") + timedelta(days=(week - 1) * int(config["week_length_days"]))
        _, even_lesson = course_numbers_for_week(
            week,
            bool(config.get("has_exam_training_lessons", False)),
        )
        if even_lesson <= 0 or not (start <= week_start <= end):
            continue
        selected.append({"week": week, "lesson": even_lesson, "date": week_start.isoformat(), "source": path.name, "payload": payload})

    status_by_user: dict[str, dict[int, str]] = {user_id: {} for user_id in roster if user_id not in refunded_ids}
    status_priority = {"无数据": 0, "未返回": 1, "未完课": 2, "已完课": 3}
    name_by_user = {user_id: str(row.get("孩子姓名") or row.get("学生姓名") or "") for user_id, row in roster.items() if user_id not in refunded_ids}
    time_by_user = {user_id: str(row.get("上课时间") or "") for user_id, row in roster.items() if user_id not in refunded_ids}
    class_by_user: dict[str, str] = {}
    for item in selected:
        for row in item["payload"].get("detailRows") or []:
            user_id = str(row.get("userId") or "").strip()
            if user_id not in status_by_user or int(row.get("lessonSort") or 0) != item["lesson"]:
                continue
            status = str(row.get("status") or "无数据")
            lesson_statuses = status_by_user[user_id]
            previous = lesson_statuses.get(item["lesson"])
            if previous is None or status_priority.get(status, 1) >= status_priority.get(previous, 1):
                lesson_statuses[item["lesson"]] = status
            if not time_by_user.get(user_id):
                class_by_user[user_id] = class_labels.get(int(row.get("classId") or 0), "")

    lesson_numbers = [item["lesson"] for item in selected]
    students: list[dict[str, Any]] = []
    for user_id, statuses in status_by_user.items():
        details = [{"lesson": lesson, "status": statuses.get(lesson, "未返回")} for lesson in lesson_numbers]
        completed = sum(item["status"] == "已完课" for item in details)
        students.append({
            "student_id": user_id,
            "student_name": name_by_user.get(user_id, ""),
            "class_time": time_by_user.get(user_id) or class_by_user.get(user_id, ""),
            "completed": completed,
            "expected": len(lesson_numbers),
            "rate": round(completed / len(lesson_numbers) * 100, 1) if lesson_numbers else 0,
            "lessons": details,
        })
    students.sort(key=lambda row: (-float(row["rate"]), str(row["class_time"]), str(row["student_name"])))
    total = len(students)
    expected_cells = total * len(lesson_numbers)
    completed_cells = sum(int(row["completed"]) for row in students)
    return {
        "period": {"year": target_year, "month": target_month, "start": start.isoformat(), "end": end.isoformat(), "label": f"{target_year}年{target_month}月"},
        "lessons": selected,
        "total_students": total,
        "completed_cells": completed_cells,
        "expected_cells": expected_cells,
        "completion_rate": round(completed_cells / expected_cells * 100, 1) if expected_cells else 0,
        "students": students,
        "checked_at": now_text(),
    }


def cleaned_course_name(raw: Any) -> str:
    text = str(raw or "").strip()
    if "-" in text:
        text = text.split("-", 1)[-1].strip()
    text = re.sub(r"\s+", "", text)
    return text or "本周课程重点"


def infer_course_topics(names: list[str]) -> list[str]:
    text = "、".join(names)
    rules = [
        (r"数组", ["一维数组", "数组下标", "数组输入输出", "数组遍历"]),
        (r"循环|for|while", ["循环结构", "循环条件", "循环变量", "循环边界"]),
        (r"分支|if|判断", ["条件判断", "if语句", "分支逻辑", "条件表达式"]),
        (r"运算|表达式|算术", ["算术运算符", "表达式计算", "运算优先级", "结果判断"]),
        (r"输入", ["输入语句", "变量接收", "数据类型", "输入格式"]),
        (r"输出|cout|换行", ["cout输出", "换行输出", "输出格式", "基础书写规范"]),
        (r"变量|数据类型", ["变量定义", "数据类型", "赋值语句", "变量使用"]),
        (r"函数", ["函数定义", "参数传递", "返回值", "函数调用"]),
        (r"字符串", ["字符串定义", "字符串下标", "字符串遍历", "常用字符串操作"]),
    ]
    topics: list[str] = []
    for pattern, values in rules:
        if re.search(pattern, text, re.IGNORECASE):
            topics.extend(values)
    for name in names:
        if name and name not in topics:
            topics.append(name)
    deduped: list[str] = []
    for topic in topics:
        if topic and topic not in deduped:
            deduped.append(topic)
    return deduped[:6] or ["本周课程重点"]


def response_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict):
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "\n".join(parts).strip()


def ai_polish_weekly_knowledge(weeks: dict[str, Any]) -> tuple[dict[str, Any], str]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_MODEL", "").strip() or os.environ.get("WORKBENCH_AI_MODEL", "").strip()
    if not api_key or not model:
        return weeks, "local"

    prompt = (
        "你是少儿编程老师。请把每周课程知识点反馈改得更自然、口语化，适合发给家长。\n"
        "只返回 JSON，不要 markdown。格式必须是："
        "{\"weeks\":{\"1\":{\"topics\":[\"...\"],\"solid\":\"...\",\"minor\":\"...\",\"weak\":\"...\"}}}。\n"
        "要求：solid 给 S 档，强调掌握好；minor 给 A+ 档，温和指出小细节；weak 给 A 档，具体提醒巩固。"
        "不要夸张，不要说孩子很差，不要编造不存在的成绩。\n\n"
        f"原始数据：{json.dumps({'weeks': weeks}, ensure_ascii=False)}"
    )
    body = json.dumps(
        {
            "model": model,
            "input": prompt,
            "temperature": 0.4,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    request = Request(
        f"{base_url}/responses",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        text = response_text(payload)
        polished = json.loads(text)
        polished_weeks = polished.get("weeks")
        if not isinstance(polished_weeks, dict):
            return weeks, "ai_invalid"
        merged = dict(weeks)
        for week, value in polished_weeks.items():
            if str(week) in merged and isinstance(value, dict):
                merged[str(week)] = {
                    **merged[str(week)],
                    "topics": value.get("topics") or merged[str(week)].get("topics") or [],
                    "solid": str(value.get("solid") or merged[str(week)].get("solid") or "").strip(),
                    "minor": str(value.get("minor") or merged[str(week)].get("minor") or "").strip(),
                    "weak": str(value.get("weak") or merged[str(week)].get("weak") or "").strip(),
                }
        return merged, "ai"
    except Exception as error:
        print(f"[ai-polish] fallback to local templates: {error}", flush=True)
        return weeks, "ai_failed"


def weekly_knowledge_suggestions() -> dict[str, Any]:
    config = script_config()
    prefix = data_prefix(config)
    candidate_paths = sorted((WORKSPACE / "data").glob(f"{prefix}-course-*-feedback.json"))
    if not candidate_paths:
        candidate_paths = sorted((WORKSPACE / "data").glob("*-course-*-feedback.json"))
    courses: dict[int, list[str]] = {}
    for path in candidate_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in payload.get("detailRows") or []:
            if not isinstance(row, dict):
                continue
            try:
                course_number = int(row.get("course_number") or 0)
            except (TypeError, ValueError):
                course_number = 0
            if course_number <= 0:
                continue
            name = cleaned_course_name(row.get("course_name"))
            if name not in courses.setdefault(course_number, []):
                courses[course_number].append(name)

    weeks: dict[str, Any] = {}
    training_enabled = bool(config.get("has_exam_training_lessons", False))
    for course_number in sorted(courses):
        regular_index = regular_course_index(course_number, training_enabled)
        if regular_index is None:
            continue
        week = (regular_index + 1) // 2
        week_key = str(week)
        names = weeks.setdefault(week_key, {"course_names": []})["course_names"]
        for name in courses[course_number]:
            if name not in names:
                names.append(name)
    for week_key, value in weeks.items():
        names = value.get("course_names") or []
        topics = infer_course_topics(names)
        topic_text = "、".join(topics[:4])
        course_text = "、".join(names[:2]) or topic_text
        value.update(
            {
                "topics": topics,
                "solid": f"这周的{course_text}学得不错，{topic_text}这些重点都能跟上，练习里也能看出孩子是理解后在做。",
                "minor": f"{course_text}这部分孩子整体能跟上，{topic_text}已经有基本掌握；后面主要把容易混淆的小细节再多练几题，会更稳。",
                "weak": f"{course_text}这部分建议课后再回看一下，重点把{topic_text}重新梳理一遍，先不用赶速度，把容易错的地方弄明白更重要。",
            }
        )
    weeks, polish_status = ai_polish_weekly_knowledge(weeks)
    return {
        "weeks": weeks,
        "source_files": [path.name for path in candidate_paths],
        "polish_status": polish_status,
    }


def summary() -> dict[str, Any]:
    config = load_config()
    metrics, fetched_at, anomalies = completion_metrics()
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
        "anomalies": anomalies,
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
        "has_exam_training_lessons": config["has_exam_training_lessons"],
        "chrome_debug_port": config["chrome_debug_port"],
        "crm_url": config["crm_url"],
        "theme": config["theme"],
        "invite": config["invite"],
        "feedback_rules": config["feedback_rules"],
        "monthly_exam_feedback": config["monthly_exam_feedback"],
        "profile": config["profile"],
    }


def monthly_exam_effective_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    settings = normalize_monthly_exam_feedback(config.get("monthly_exam_feedback"))
    source = Path(settings["source_dir"]).expanduser()
    if not source.is_absolute():
        source = (WORKSPACE / source).resolve()
    templates_dir = Path(settings["templates_dir"]) if settings["templates_dir"] else source / "话术"
    if not templates_dir.is_absolute():
        templates_dir = (source / templates_dir).resolve()
    templates = dict(settings["templates"])
    teacher_name = settings["teacher_name"]
    teacher_file = templates_dir / "tt.txt"
    if not teacher_name and teacher_file.is_file():
        teacher_name = teacher_file.read_text(encoding="utf-8-sig").strip()
    for band in MONTHLY_EXAM_BANDS:
        if not templates[band]:
            path = templates_dir / f"{band}.txt"
            if path.is_file():
                templates[band] = path.read_text(encoding="utf-8-sig").strip()
    settings["source_dir"] = str(source)
    settings["templates_dir"] = str(templates_dir)
    settings["teacher_name"] = teacher_name
    settings["templates"] = templates
    return settings


def monthly_exam_path(value: str, base: Path) -> Path:
    path = Path(str(value or "").strip()).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def monthly_exam_manifest_path() -> Path:
    return MONTHLY_EXAM_RUNTIME / "manifest.json"


def monthly_exam_sent_status_path() -> Path:
    return MONTHLY_EXAM_RUNTIME / "sent-status.json"


def monthly_exam_score_signature(manifest: dict[str, Any] | None) -> str:
    return str((manifest or {}).get("score_workbook") or "").strip()


def monthly_exam_scores_match(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) < 0.0001
    except (TypeError, ValueError):
        return str(left or "").strip() == str(right or "").strip()


def monthly_exam_band_for(score: float) -> str | None:
    return next((name for low, high, name in MONTHLY_EXAM_BAND_RANGES if low <= score <= high), None)


def monthly_exam_format_score(score: Any) -> str:
    try:
        number = float(score)
        return str(int(number)) if number.is_integer() else str(number)
    except (TypeError, ValueError):
        return str(score or "").strip()


def monthly_exam_protective_score(score: Any) -> float | None:
    try:
        number = float(score)
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 100:
        return None
    if number < 50:
        return 60.0
    if number < 60:
        return 65.0
    if number in (70.0, 75.0):
        return 80.0
    return number


def render_monthly_exam_template(template: str, values: dict[str, Any]) -> str:
    rendered = template.replace("xx", str(values["student_name"]))
    rendered = rendered.replace("ss", str(values["score"]))
    rendered = rendered.replace("tt", str(values["teacher_name"]))
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered.strip()


def load_monthly_exam_sent_status() -> dict[str, Any]:
    path = monthly_exam_sent_status_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_monthly_exam_sent_status(payload: dict[str, Any]) -> None:
    path = monthly_exam_sent_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def monthly_exam_result_matches_row(result: dict[str, Any], row: dict[str, Any]) -> bool:
    # 已发送判定只按学生 ID（+姓名核对），不比较分数：分数可能因改原始分/保护分而变，
    # 已反馈过的学生不应因为分数变化而回退为"可发送"。
    result_name = str(result.get("student_name") or "").strip()
    row_name = str(row.get("student_name") or "").strip()
    if result_name and row_name and result_name != row_name:
        return False
    return True


def sync_monthly_exam_sent_status(manifest: dict[str, Any]) -> dict[str, Any]:
    status = load_monthly_exam_sent_status()
    score_signature = monthly_exam_score_signature(manifest)
    rows = {str(item.get("student_id") or "").strip(): item for item in manifest.get("students") or [] if str(item.get("student_id") or "").strip()}
    result_dir = MONTHLY_EXAM_RUNTIME / "results"
    changed = False
    if result_dir.is_dir():
        for result_path in result_dir.glob("*.json"):
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(result, dict) or result.get("created") is not True:
                continue
            student_id = str(result.get("student_id") or result_path.stem).strip()
            row = rows.get(student_id)
            if not row or not monthly_exam_result_matches_row(result, row):
                continue
            sent_at = str(result.get("created_at") or result.get("generated_at") or "").strip()
            if not sent_at:
                sent_at = datetime.fromtimestamp(result_path.stat().st_mtime).isoformat(timespec="seconds")
            next_item = {
                "student_id": student_id,
                "student_name": str(row.get("student_name") or result.get("student_name") or "").strip(),
                "score": row.get("score"),
                "score_workbook": score_signature,
                "sent_at": sent_at,
                "result_file": str(result_path),
            }
            if status.get(student_id) != next_item:
                status[student_id] = next_item
                changed = True
    if changed:
        save_monthly_exam_sent_status(status)
    return status


def annotate_monthly_exam_manifest(manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(manifest, dict):
        return manifest
    status = sync_monthly_exam_sent_status(manifest)
    score_signature = monthly_exam_score_signature(manifest)
    sent_count = 0
    ready_unsent_count = 0
    for row in manifest.get("students") or []:
        student_id = str(row.get("student_id") or "").strip()
        item = status.get(student_id) if student_id else None
        # 已发送判定只认学生 ID（该学生曾创建过任务即已发送），
        # 不再比较成绩表签名/分数，避免改原始分或换成绩表后已反馈学生回退为可发送。
        sent = isinstance(item, dict) and str(item.get("student_id") or "").strip() == student_id
        row["sent"] = bool(sent)
        row["sent_at"] = str(item.get("sent_at") or "") if sent else ""
        if sent:
            sent_count += 1
        elif row.get("send_ready") is True:
            ready_unsent_count += 1
    manifest["sent_count"] = sent_count
    manifest["ready_unsent_count"] = ready_unsent_count
    manifest["adjusted_score_count"] = sum(1 for row in manifest.get("students") or [] if row.get("display_score_adjusted") is True)
    return manifest


def apply_monthly_exam_protective_scores_to_manifest(
    manifest: dict[str, Any],
    settings: dict[str, Any] | None = None,
) -> int:
    settings = settings or monthly_exam_effective_settings(load_config())
    templates = settings.get("templates") if isinstance(settings.get("templates"), dict) else {}
    teacher_name = str(manifest.get("teacher_name") or settings.get("teacher_name") or "").strip()
    changed = 0
    for row in manifest.get("students") or []:
        if not isinstance(row, dict):
            continue
        original_score = row.get("original_score", row.get("score"))
        protected_score = monthly_exam_protective_score(original_score)
        if protected_score is None:
            continue
        current_is_original = monthly_exam_scores_match(row.get("score"), original_score)
        current_is_protected = monthly_exam_scores_match(row.get("score"), protected_score)
        if not current_is_original and not current_is_protected:
            continue
        if monthly_exam_scores_match(protected_score, original_score):
            row["display_score_adjusted"] = False
            row.pop("score_adjustment_note", None)
            continue
        protected_band = monthly_exam_band_for(protected_score)
        if not protected_band:
            continue
        row["original_score"] = original_score
        row["score"] = protected_score
        row["band"] = protected_band
        row["display_score_adjusted"] = True
        row["score_adjustment_note"] = f"原始 {monthly_exam_format_score(original_score)} → 展示 {monthly_exam_format_score(protected_score)}"
        # 错题按展示分重新计算：展示分 P → 错题率 (1 - P/100)，保留错得最严重的题，
        # 使错题数与展示分一致（仅影响清单与话术，不改成绩文件）
        new_wrong: list[int] = []
        details = row.get("question_details") if isinstance(row.get("question_details"), list) else []
        if details and protected_score is not None:
            total_questions = len(details)
            protected_wrong_count = max(0, round(total_questions * (1.0 - float(protected_score) / 100.0)))
            wrong_items: list[tuple[float, int]] = []
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                question_number = int(detail.get("question") or 0)
                detail_score = float(detail.get("score") or 0.0)
                maximum = float(detail.get("max") or 0.0)
                if question_number > 0 and maximum > 0 and detail_score < maximum:
                    wrong_items.append((detail_score, question_number))
            wrong_items.sort(key=lambda item: (item[0], item[1]))
            new_wrong = [question for _, question in wrong_items[:protected_wrong_count]]
        row["wrong_questions"] = new_wrong
        row["wrong_count"] = len(new_wrong)
        wrong_questions = new_wrong
        wrong_count = len(new_wrong)
        question_count = max(wrong_count, int(row.get("question_count") or 20))
        values = {
            "student_name": row.get("student_name") or "",
            "score": monthly_exam_format_score(protected_score),
            "wrong_count": wrong_count,
            "correct_count": max(0, question_count - wrong_count),
            "question_count": question_count,
            "wrong_questions": "、".join(str(value) for value in wrong_questions) or "无",
            "teacher_name": teacher_name,
        }
        template = str(templates.get(protected_band) or "")
        if template:
            message = render_monthly_exam_template(template, values)
            row["message"] = message
            message_file = Path(str(row.get("message_file") or ""))
            if message_file.is_absolute():
                message_file.parent.mkdir(parents=True, exist_ok=True)
                message_file.write_text(message, encoding="utf-8")
        refresh_monthly_exam_material_requirements(row, settings)
        if current_is_original:
            changed += 1
    manifest["score_adjustment"] = {
        "mode": "protective_display_only",
        "applied_at": datetime.now().isoformat(timespec="seconds"),
        "changed_count": changed,
        "rules": [
            "原始成绩 < 50：反馈展示为 60",
            "原始成绩 50-59：反馈展示为 65",
            "原始成绩 70 或 75：反馈展示为 80",
            "错题明细按展示分重新计算（展示分越高错题越少），错题数与展示分一致",
            "原始成绩文件不被修改；错题报告会按当前预览清单重新生成",
        ],
    }
    return changed


def refresh_monthly_exam_material_requirements(row: dict[str, Any], settings: dict[str, Any]) -> None:
    """Refresh PDF/award attachment fields after score or wrong-count changes."""
    if not isinstance(row, dict):
        return
    source = Path(settings["source_dir"])
    student_name = str(row.get("student_name") or "").strip()
    blockers = [
        str(value).strip()
        for value in (row.get("blockers") if isinstance(row.get("blockers"), list) else [])
        if str(value).strip()
    ]
    material_blockers = {
        "有错题但缺少同名错题解析PDF",
    }
    blockers = [
        value
        for value in blockers
        if value not in material_blockers and not value.startswith("缺少同名奖状图片（")
    ]

    try:
        score = float(row.get("score"))
    except (TypeError, ValueError):
        score = None
    try:
        wrong_count = int(row.get("wrong_count") or 0)
    except (TypeError, ValueError):
        wrong_count = 0

    if settings.get("send_wrong_report") and wrong_count > 0 and student_name:
        pdf = monthly_exam_path(settings["pdf_dir"], source) / f"{student_name}_错题解析.pdf"
        row["pdf"] = str(pdf)
        if not pdf.is_file():
            blockers.append("有错题但缺少同名错题解析PDF")
    else:
        row["pdf"] = ""

    award_threshold = clamp_int(settings.get("award_threshold"), 0, 100, 70)
    # 是否带奖状严格按分数阈值判定（>= 阈值即生成并发送奖状），不受开关影响
    if score is not None and score >= award_threshold and student_name:
        award = monthly_exam_path(settings["award_dir"], source) / f"{student_name}_奖状.png"
        row["award"] = str(award)
        if not award.is_file():
            blockers.append(f"缺少同名奖状图片（{award_threshold}分及以上学员需奖状）")
    else:
        row["award"] = ""

    row["blockers"] = blockers
    row["send_ready"] = not blockers


def apply_monthly_exam_protective_scores() -> dict[str, Any]:
    manifest_path = monthly_exam_manifest_path()
    if not manifest_path.is_file():
        raise RuntimeError("请先生成月考反馈预览，再使用保护展示分")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("当前月考反馈预览读取失败，请重新生成预览") from error
    if not isinstance(manifest, dict):
        raise RuntimeError("当前月考反馈预览格式异常，请重新生成预览")

    config = save_config({"monthly_exam_feedback": {"protective_score_enabled": True}})
    changed = apply_monthly_exam_protective_scores_to_manifest(
        manifest,
        monthly_exam_effective_settings(config),
    )
    annotate_monthly_exam_manifest(manifest)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "config": monthly_exam_effective_settings(config),
        "manifest": manifest,
        "changed_count": changed,
    }


def run_monthly_exam_preview(config: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = monthly_exam_effective_settings(config)
    source = Path(settings["source_dir"])
    if not source.is_dir():
        raise RuntimeError(f"月考反馈目录不存在：{source}")
    if not MONTHLY_EXAM_PREPARE.is_file():
        raise RuntimeError(f"工作台缺少月考反馈解析模块：{MONTHLY_EXAM_PREPARE}")
    runtime_templates = MONTHLY_EXAM_RUNTIME / "templates"
    runtime_templates.mkdir(parents=True, exist_ok=True)
    for band in MONTHLY_EXAM_BANDS:
        text = settings["templates"].get(band, "").strip()
        if not text:
            raise RuntimeError(f"五档话术“{band}”为空，请先在本页配置后再预览")
        (runtime_templates / f"{band}.txt").write_text(text + "\n", encoding="utf-8")
    (runtime_templates / "tt.txt").write_text(settings["teacher_name"] + "\n", encoding="utf-8")
    MONTHLY_EXAM_RUNTIME.mkdir(parents=True, exist_ok=True)
    command = [
        *PYTHON, str(MONTHLY_EXAM_PREPARE), "--source-dir", str(source),
        "--templates-dir", str(runtime_templates), "--pdf-dir",
        str(monthly_exam_path(settings["pdf_dir"], source)), "--award-dir",
        str(monthly_exam_path(settings["award_dir"], source)), "--teacher-name",
        settings["teacher_name"], "--output-dir", str(MONTHLY_EXAM_RUNTIME),
    ]
    if settings["score_file"]:
        command.extend(["--score-file", settings["score_file"]])
    roster = monthly_exam_path(settings["roster_json"], WORKSPACE)
    if roster.is_file():
        command.extend(["--roster-json", str(roster)])
    # 奖状不再受 send_award 开关拦截：是否带奖状由 prepare 的 award 字段（按阈值）决定
    if not settings["send_wrong_report"]:
        command.append("--no-wrong-report")
    result = subprocess.run(
        command, cwd=WORKSPACE, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_CONSOLE_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError("月考反馈预览失败：\n" + result.stdout[-5000:])
    manifest = json.loads(monthly_exam_manifest_path().read_text(encoding="utf-8"))
    if settings.get("protective_score_enabled"):
        apply_monthly_exam_protective_scores_to_manifest(manifest, settings)
        monthly_exam_manifest_path().write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    annotate_monthly_exam_manifest(manifest)
    manifest["preview_output"] = result.stdout[-5000:]
    return {"config": settings, "manifest": manifest}


def monthly_exam_status() -> dict[str, Any]:
    config = load_config()
    payload: dict[str, Any] = {"config": monthly_exam_effective_settings(config), "manifest": None}
    manifest_path = monthly_exam_manifest_path()
    if manifest_path.is_file():
        try:
            payload["manifest"] = annotate_monthly_exam_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            payload["manifest"] = None
    return payload


def start_monthly_exam_send(student_ids: list[str]) -> dict[str, Any]:
    ids = list(dict.fromkeys(str(value).strip() for value in student_ids if str(value).strip()))
    if not ids:
        raise RuntimeError("请至少选择一名可发送学员")
    preview = run_monthly_exam_preview()
    rows = {str(item.get("student_id")): item for item in preview["manifest"].get("students") or []}
    missing = [value for value in ids if value not in rows]
    already_sent = [value for value in ids if value in rows and rows[value].get("sent") is True]
    already_sent_set = set(already_sent)
    send_ids = [value for value in ids if value not in already_sent_set]
    blocked = [rows[value] for value in send_ids if value in rows and rows[value].get("send_ready") is not True]
    if missing:
        raise RuntimeError("清单中没有找到学生ID：" + "、".join(missing[:12]))
    if blocked:
        details = []
        for item in blocked[:12]:
            details.append(f"{item.get('student_name') or item.get('student_id')}：{'；'.join(item.get('blockers') or [])}")
        raise RuntimeError("所选学员仍有校验问题：" + " | ".join(details))
    if not send_ids:
        raise RuntimeError("所选学员都已经创建过月考反馈任务，无需重复发送")
    if not MONTHLY_EXAM_SEND.is_file():
        raise RuntimeError(f"工作台缺少企微发送模块：{MONTHLY_EXAM_SEND}")
    result_dir = MONTHLY_EXAM_RUNTIME / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    task = Task(
        "monthly_exam_feedback_send", f"发送月考反馈（{len(send_ids)}人）",
        "按预览清单为已确认学员创建企微待发送任务。最终发送仍需在企微客户端确认。",
        "月考反馈", tuple(), True,
        "将为所选学员上传对应错题报告/奖状并创建企微待发送任务；请确认姓名、分数和附件均已核对。",
    )
    job = JOBS.create(task)
    commands: list[tuple[str, ...]] = []
    for student_id in send_ids:
        result_path = result_dir / f"{student_id}.json"
        commands.append(tuple([
            *PYTHON, str(MONTHLY_EXAM_SEND), "--workspace", str(WORKSPACE),
            "--manifest", str(monthly_exam_manifest_path()), "--student-id", student_id,
            "--result", str(result_path), "--execute",
        ]))
    threading.Thread(target=run_job, args=(job["id"], task, tuple(commands)), daemon=True).start()
    return {"job_id": job["id"], "selected_count": len(send_ids), "student_ids": send_ids, "skipped_sent": already_sent}


def start_monthly_exam_cancel(student_ids: list[str]) -> dict[str, Any]:
    ids = list(dict.fromkeys(str(value).strip() for value in student_ids if str(value).strip()))
    if not ids:
        raise RuntimeError("请至少选择一名已发送学员")
    payload = monthly_exam_status()
    manifest = payload.get("manifest") if isinstance(payload, dict) else None
    if not isinstance(manifest, dict):
        raise RuntimeError("请先生成月考反馈预览，再取消月考反馈")
    rows = {str(item.get("student_id") or "").strip(): item for item in manifest.get("students") or []}
    missing = [value for value in ids if value not in rows]
    if missing:
        raise RuntimeError("清单中没有找到学生ID：" + "、".join(missing[:12]))
    cancel_ids = [value for value in ids if rows[value].get("sent") is True]
    skipped_not_sent = [value for value in ids if value not in set(cancel_ids)]
    if not cancel_ids:
        raise RuntimeError("所选学员没有已创建的月考反馈任务，无需取消")
    if not MONTHLY_EXAM_CANCEL.is_file():
        raise RuntimeError(f"工作台缺少月考反馈取消模块：{MONTHLY_EXAM_CANCEL}")
    task = Task(
        "monthly_exam_feedback_cancel", f"取消月考反馈（{len(cancel_ids)}人）",
        "仅取消月考反馈对应的企微待发送任务；取消成功后本地标记恢复为可发送。",
        "月考反馈", tuple(), True,
        "将取消所选学员的月考反馈企微待发送任务；如果家长侧已经在企微客户端确认发送，CRM 会拒绝取消。",
    )
    job = JOBS.create(task)
    command: list[str] = [
        *PYTHON, str(MONTHLY_EXAM_CANCEL), "--workspace", str(WORKSPACE), "--execute",
    ]
    for student_id in cancel_ids:
        command.extend(["--student-id", student_id])
    threading.Thread(target=run_job, args=(job["id"], task, (tuple(command),)), daemon=True).start()
    return {
        "job_id": job["id"],
        "selected_count": len(cancel_ids),
        "student_ids": cancel_ids,
        "skipped_not_sent": skipped_not_sent,
    }


def run_monthly_exam_unreplied(class_code: str = "", since_days: int = 0) -> dict[str, Any]:
    """Detect parents who sent the latest message but the teacher has not replied.

    Scans local parent-chat captures (data/parent-chats*) via
    check_unreplied_parents.py, optionally filtering by class and recent days.
    """
    if not MONTHLY_EXAM_UNREPLIED.is_file():
        raise RuntimeError(f"工作台缺少未回复检测模块：{MONTHLY_EXAM_UNREPLIED}")
    out_json = MONTHLY_EXAM_RUNTIME / "unreplied-parents.json"
    out_csv = MONTHLY_EXAM_RUNTIME / "unreplied-parents.csv"
    command = [*PYTHON, str(MONTHLY_EXAM_UNREPLIED), "--out", str(out_json), "--csv", str(out_csv)]
    if class_code:
        command.extend(["--class-code", class_code])
    if since_days > 0:
        command.extend(["--since-days", str(int(since_days))])
    result = subprocess.run(
        command, cwd=WORKSPACE, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_CONSOLE_WINDOW, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError("未回复检测失败：\n" + result.stdout[-3000:])
    try:
        payload = json.loads(out_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {"unreplied_count": 0, "students": []}
    payload["preview_output"] = result.stdout[-1500:]
    return {"count": payload.get("unreplied_count", 0), "students": payload.get("students", []), "output": result.stdout[-1500:]}


def start_monthly_exam_generate() -> dict[str, Any]:
    """Generate wrong-question reports and awards from the workbench manifest.

    Uses the preview manifest (display/protective score and recalculated wrong
    questions) as the single source of truth. Reports are regenerated with
    --force so the wrong-question counts always match the display score.
    """
    if not MONTHLY_EXAM_GEN_ALL.is_file():
        raise RuntimeError("工作台缺少月考反馈物料生成模块（generate_report_and_award.py）")
    if not MONTHLY_EXAM_DEPS.is_file():
        raise RuntimeError("工作台缺少月考反馈依赖检查模块（ensure_monthly_exam_dependencies.py）")
    settings = monthly_exam_effective_settings()
    source = Path(settings["source_dir"])
    if not source.is_dir():
        raise RuntimeError(f"月考反馈目录不存在：{source}")
    if not MONTHLY_EXAM_RUNTIME.is_dir() or not (MONTHLY_EXAM_RUNTIME / "manifest.json").is_file():
        raise RuntimeError("请先生成月考反馈预览，再生成物料（需要成绩清单用于错题数/奖状阈值判定）")
    task = Task(
        "monthly_exam_generate", "生成错题报告与奖状",
        f"按当前预览清单（展示分与重算错题数）生成全班错题解析报告，并为 {settings.get('award_threshold', 70)} 分及以上学员生成奖状；保存到月考文件夹原位置。",
        "月考反馈", tuple(), True,
        f"将按当前预览清单的展示分（保护分以保护分为准）和重算后的错题数，重新生成全班错题解析报告并补齐 {settings.get('award_threshold', 80)} 分及以上学员的奖状（已有奖状跳过）。保存位置不变：月考文件夹「全班错题报告」「已生成奖状」。报告约需数分钟，奖状渲染较慢，日志实时显示。",
    )
    job = JOBS.create(task)
    command = tuple([
        *PYTHON, str(MONTHLY_EXAM_GEN_ALL),
        "--source-dir", str(source),
        "--manifest", str(MONTHLY_EXAM_RUNTIME / "manifest.json"),
        "--award-threshold", str(settings.get("award_threshold", 70)),
        "--force",
    ])
    deps_command = tuple([*PYTHON, str(MONTHLY_EXAM_DEPS)])
    threading.Thread(target=run_job, args=(job["id"], task, (deps_command, command)), daemon=True).start()
    return {"job_id": job["id"]}


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


def restart_workbench() -> dict[str, Any]:
    command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]

    def launch_new_process() -> None:
        time.sleep(0.45)
        subprocess.Popen(command, cwd=WORKSPACE, close_fds=True, creationflags=NO_CONSOLE_WINDOW)
        os._exit(0)

    threading.Thread(target=launch_new_process, daemon=True).start()
    return {"success": True, "message": "正在重启工作台并加载最新代码"}


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
            creationflags=NO_CONSOLE_WINDOW,
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
        creationflags=NO_CONSOLE_WINDOW,
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


def task_sort_key(task: Task) -> tuple[int, str, str]:
    if task.task_id in TASK_ORDER:
        return (TASK_ORDER[task.task_id], task.group, task.title)
    return (1000, task.group, task.title)


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
                creationflags=NO_CONSOLE_WINDOW,
            )
            JOBS.register_process(job_id, process)
            assert process.stdout is not None
            for line in process.stdout:
                JOBS.append(job_id, line)
            exit_code = process.wait()
            JOBS.unregister_process(job_id, process)
        except Exception as error:
            JOBS.append(job_id, f"启动失败：{error}")
            exit_code = 1
        if JOBS.stop_requested(job_id):
            JOBS.append(job_id, "任务已暂停，后续步骤不会继续执行。")
            break
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
        f"[{now_text()}] {'已暂停' if JOBS.stop_requested(job_id) else '完成' if exit_code == 0 else '失败'}：{task.title}",
    )
    JOBS.finish(job_id, exit_code)


class Handler(BaseHTTPRequestHandler):
    server_version = "CodeMaoTeacherWorkbench/1.0"

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} {format_string % args}")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/tasks":
            ordered_tasks = sorted(TASKS.values(), key=task_sort_key)
            self.send_json({"tasks": [task_payload(task) for task in ordered_tasks]})
            return
        if parsed.path == "/api/schedules":
            self.send_json(public_schedules())
            return
        if parsed.path == "/api/summary":
            self.send_json(summary())
            return
        if parsed.path == "/api/trends":
            self.send_json(weekly_trends())
            return
        if parsed.path == "/api/student-risks":
            self.send_json(student_risk_snapshot())
            return
        if parsed.path == "/api/performance":
            self.send_json(monthly_performance(parse_qs(parsed.query)))
            return
        if parsed.path == "/api/monthly-exam":
            self.send_json(monthly_exam_status())
            return
        if parsed.path == "/api/feedback-knowledge-suggestions":
            self.send_json(weekly_knowledge_suggestions())
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
        if parsed.path == "/api/restart":
            if not self.valid_local_request():
                self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
                return
            self.send_json(restart_workbench(), HTTPStatus.ACCEPTED)
            return
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
        if parsed.path == "/api/jobs/stop":
            if not self.valid_local_request():
                self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                job = JOBS.request_stop(str(payload.get("job_id") or "") or None)
            except RuntimeError as error:
                self.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
                return
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "请求内容不是有效 JSON"}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"success": True, "job": job}, HTTPStatus.ACCEPTED)
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
        if parsed.path == "/api/monthly-exam/config":
            if not self.valid_local_request():
                self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("配置内容必须是对象")
                config = save_config({"monthly_exam_feedback": payload})
                self.send_json({"success": True, "config": monthly_exam_effective_settings(config), "message": "月考反馈配置已保存"})
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except Exception as error:
                self.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
            return
        if parsed.path == "/api/monthly-exam/preview":
            if not self.valid_local_request():
                self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if isinstance(payload, dict) and isinstance(payload.get("config"), dict):
                    save_config({"monthly_exam_feedback": payload["config"]})
                self.send_json({"success": True, **run_monthly_exam_preview()}, HTTPStatus.OK)
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except Exception as error:
                self.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
            return
        if parsed.path == "/api/monthly-exam/score-adjustment":
            if not self.valid_local_request():
                self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict) or payload.get("confirmed") is not True:
                    raise ValueError("生成保护展示分需要明确确认")
                self.send_json({"success": True, **apply_monthly_exam_protective_scores()}, HTTPStatus.OK)
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except Exception as error:
                self.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
            return
        if parsed.path == "/api/monthly-exam/send":
            if not self.valid_local_request():
                self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict) or payload.get("confirmed") is not True:
                    raise ValueError("发送月考反馈需要明确确认")
                student_ids = payload.get("student_ids")
                if not isinstance(student_ids, list):
                    raise ValueError("student_ids 必须是数组")
                self.send_json({"success": True, **start_monthly_exam_send([str(value) for value in student_ids])}, HTTPStatus.ACCEPTED)
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except Exception as error:
                self.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
            return
        if parsed.path == "/api/monthly-exam/cancel":
            if not self.valid_local_request():
                self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict) or payload.get("confirmed") is not True:
                    raise ValueError("取消月考反馈需要明确确认")
                student_ids = payload.get("student_ids")
                if not isinstance(student_ids, list):
                    raise ValueError("student_ids 必须是数组")
                self.send_json({"success": True, **start_monthly_exam_cancel([str(value) for value in student_ids])}, HTTPStatus.ACCEPTED)
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except Exception as error:
                self.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
            return
        if parsed.path == "/api/monthly-exam/unreplied":
            if not self.valid_local_request():
                self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                class_code = str(payload.get("class_code") or "")
                since_days = int(payload.get("since_days") or 0)
                self.send_json({"success": True, **run_monthly_exam_unreplied(class_code, since_days)}, HTTPStatus.OK)
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except Exception as error:
                self.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
            return
        if parsed.path == "/api/monthly-exam/generate":
            if not self.valid_local_request():
                self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict) or payload.get("confirmed") is not True:
                    raise ValueError("生成月考反馈物料需要明确确认")
                self.send_json({"success": True, **start_monthly_exam_generate()}, HTTPStatus.ACCEPTED)
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except Exception as error:
                self.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
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
