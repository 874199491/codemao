#!/usr/bin/env python3
"""Refresh one schedule's solitaire data, then rebuild invite follow-up rows."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from teacher_workbench_config import (
    class_mappings,
    data_path,
    data_prefix,
    load_workbench_config,
    script_config,
)
from week_context import context_for


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPTS = WORKSPACE / "scripts"
DATA = WORKSPACE / "data"
CONFIG = script_config()
WORKBENCH_CONFIG = load_workbench_config()
PREFIX = data_prefix(CONFIG)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--class-prefix", required=True, help="例如：周五 / 周六")
    parser.add_argument(
        "--port",
        type=int,
        default=int(WORKBENCH_CONFIG.get("chrome_debug_port") or 9223),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int((WORKBENCH_CONFIG.get("invite") or {}).get("workers") or 6),
    )
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--skip-solitaire-fetch",
        action="store_true",
        help="使用本地已有接龙抓取结果，主要用于调试。",
    )
    return parser.parse_args()


def run(command: list[str]) -> None:
    print("$ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=WORKSPACE, check=True)


def class_code() -> str:
    candidates = [
        WORKBENCH_CONFIG.get("solitaire_class_code"),
        WORKBENCH_CONFIG.get("class_code"),
        CONFIG.get("class_code"),
        CONFIG.get("data_prefix"),
        CONFIG.get("cohort_code"),
        WORKBENCH_CONFIG.get("cohort_code"),
        PREFIX,
    ]
    for value in candidates:
        code = str(value or "").strip()
        if code:
            return code
    raise RuntimeError(
        "缺少接龙群搜索用的班期标识：请在配置里填写 cohort_code 或 profile.data_prefix，例如 0807。"
    )


def schedule_key(class_prefix: str) -> str:
    if "\u5468\u4e94" in class_prefix:
        return "friday"
    if "\u5468\u516d" in class_prefix:
        return "saturday"
    safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", class_prefix).strip("-")
    return safe or "schedule"


def minimum_group_count(class_prefix: str) -> int:
    labels = [label for _, label in class_mappings(CONFIG)]
    if "\u5468\u4e94" in class_prefix:
        return max(1, sum(1 for label in labels if "\u5468\u4e94" in label))
    if "\u5468\u516d" in class_prefix:
        return max(1, sum(1 for label in labels if "\u5468\u516d" in label))
    return max(1, len(class_time_labels(class_prefix)))


def class_time_labels(class_prefix: str) -> list[str]:
    labels = [label for _, label in class_mappings(CONFIG)]
    matched = [
        label
        for label in labels
        if label.startswith(class_prefix) or class_prefix.startswith(label)
    ]
    if not matched:
        normalized_prefix = class_prefix[:2]
        matched = [label for label in labels if label.startswith(normalized_prefix)]
    if not matched:
        raise RuntimeError(f"找不到与 {class_prefix!r} 匹配的上课时间配置：{labels}")
    return matched


def solitaire_path(week: int, class_prefix: str) -> Path:
    return DATA / f"{PREFIX}-week{week}-solitaire-{schedule_key(class_prefix)}" / "latest.json"


def fetch_solitaire(args: argparse.Namespace) -> Path:
    context = context_for(week=args.week)
    since = f"{context.start.isoformat()}T00:00:00+08:00"
    until = f"{(context.end + timedelta(days=1)).isoformat()}T00:00:00+08:00"
    output = solitaire_path(args.week, args.class_prefix)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not args.skip_solitaire_fetch:
        run(
            [
                "node",
                str(SCRIPTS / "fetch_group_solitaire_from_crm.mjs"),
                f"--port={args.port}",
                f"--class-code={class_code()}",
                f"--group-keyword={args.class_prefix}",
                f"--since={since}",
                f"--until={until}",
                f"--out-dir={output.parent}",
                f"--roster={data_path('students_json', CONFIG)}",
            ]
        )
    if not output.exists():
        raise RuntimeError(f"接龙抓取结果不存在：{output}")
    payload = json.loads(output.read_text(encoding="utf-8"))
    group_count = len(payload.get("groups") or [])
    minimum = minimum_group_count(args.class_prefix)
    if group_count < minimum:
        raise RuntimeError(
            f"W{args.week} {args.class_prefix}接龙群只找到 {group_count} 个，"
            f"未达到预期 {minimum} 个；已停止，未写入邀约跟进。"
        )
    return output


def sync_solitaire(args: argparse.Namespace, source: Path) -> None:
    run(
        [
            "py",
            "-3.10",
            str(SCRIPTS / "ensure_week_learning_columns.py"),
            "--week",
            str(args.week),
        ]
    )
    command = [
        "py",
        "-3.10",
        str(SCRIPTS / "update_weekly_solitaire.py"),
        "--week",
        str(args.week),
        "--solitaire",
        str(source),
    ]
    for label in class_time_labels(args.class_prefix):
        command.extend(["--class-time", label])
    if args.check_only:
        command.append("--check-only")
    run(command)


def update_invite_followup(args: argparse.Namespace) -> None:
    command = [
        "py",
        "-3.10",
        str(SCRIPTS / "update_weekly_invite_followup.py"),
        "--week",
        str(args.week),
        "--class-prefix",
        args.class_prefix,
        "--workers",
        str(args.workers),
        "--port",
        str(args.port),
    ]
    if args.check_only:
        command.append("--check-only")
    run(command)


def main() -> int:
    args = parse_args()
    started = datetime.now().isoformat(timespec="seconds")
    source = fetch_solitaire(args)
    sync_solitaire(args, source)
    update_invite_followup(args)
    print(
        json.dumps(
            {
                "startedAt": started,
                "finishedAt": datetime.now().isoformat(timespec="seconds"),
                "week": args.week,
                "classPrefix": args.class_prefix,
                "solitaire": str(source),
                "classTimes": class_time_labels(args.class_prefix),
                "checkOnly": bool(args.check_only),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
