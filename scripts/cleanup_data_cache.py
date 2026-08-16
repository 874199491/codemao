from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
DATA = WORKSPACE / "data"
CONFIG = DATA / "teacher-workbench-config.json"

PROTECTED_NAMES = {
    "teacher-workbench-config.json",
    "workbench-update-source.json",
    "workbench-schedules.json",
    "crm-cookies.json",
    "wecom-parent-map-cache.json",
    "feedback-style-config.json",
}

PROTECTED_PREFIXES = (
    "new-class-student-list",
    "teacher-profile",
)

CACHE_SUFFIXES = {
    ".csv",
    ".json",
    ".jsonl",
    ".log",
    ".txt",
    ".tmp",
}


@dataclass(frozen=True)
class Candidate:
    path: Path
    reason: str
    size: int
    files: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean regenerable teacher workbench data cache.")
    parser.add_argument("--apply", action="store_true", help="Actually delete files. Without this flag, only previews.")
    parser.add_argument("--keep-weeks", type=int, default=4, help="Keep this many latest cohort weeks.")
    parser.add_argument("--keep-days", type=int, default=35, help="Keep date-based caches newer than this many days.")
    return parser.parse_args()


def read_config() -> dict:
    if not CONFIG.exists():
        return {}
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def parse_date(value: object, fallback: date) -> date:
    text = str(value or "").strip().replace("/", "-")
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return fallback


def current_week(config: dict) -> int:
    start = parse_date(config.get("cohort_start"), date.today())
    active_days = int(config.get("week_active_days") or 5)
    length_days = int(config.get("week_length_days") or 7)
    days = (date.today() - start).days
    if days < 0:
        return 1
    # The workbench treats each week as the active Thu-Mon window, then jumps by week_length_days.
    return max(1, days // max(1, length_days) + 1 if days >= active_days else 1)


def size_of(path: Path) -> tuple[int, int]:
    if path.is_file():
        return path.stat().st_size, 1
    total = 0
    files = 0
    for item in path.rglob("*"):
        if item.is_file():
            files += 1
            total += item.stat().st_size
    return total, files


def week_number(name: str) -> int | None:
    import re

    match = re.search(r"(?:^|[-_])week(\d+)(?:[-_]|$)", name, re.IGNORECASE)
    if not match:
        match = re.search(r"(?:^|[-_])w(\d+)(?:[-_]|$)", name, re.IGNORECASE)
    return int(match.group(1)) if match else None


def is_protected(path: Path) -> bool:
    if path.name in PROTECTED_NAMES:
        return True
    return any(path.name.startswith(prefix) for prefix in PROTECTED_PREFIXES)


def is_within_data(path: Path) -> bool:
    try:
        path.resolve().relative_to(DATA.resolve())
        return True
    except ValueError:
        return False


def should_clean(path: Path, *, keep_from_week: int, cutoff: datetime) -> str | None:
    if not is_within_data(path) or is_protected(path):
        return None

    name = path.name
    week = week_number(name)
    if week is not None and week < keep_from_week:
        return f"旧周缓存：W{week} < W{keep_from_week}"

    if name.startswith("parent-chats"):
        if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
            return f"家长聊天历史缓存早于 {cutoff:%Y-%m-%d}"
        return None

    if name.startswith("archives"):
        if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
            return f"归档缓存早于 {cutoff:%Y-%m-%d}"
        return None

    if path.is_file():
        if path.suffix.lower() not in CACHE_SUFFIXES:
            return None
        if ".before-" in name or "rerun" in name.lower():
            return "历史重跑/备份缓存"
        if "-query-20" in name and datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
            return f"旧查询缓存早于 {cutoff:%Y-%m-%d}"
        if name.startswith("crm-group-message") and datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
            return f"旧群消息缓存早于 {cutoff:%Y-%m-%d}"

    return None


def collect_candidates(keep_weeks: int, keep_days: int) -> list[Candidate]:
    config = read_config()
    current = current_week(config)
    keep_from_week = max(1, current - max(1, keep_weeks) + 1)
    cutoff = datetime.now() - timedelta(days=max(1, keep_days))

    if not DATA.exists():
        return []

    candidates: list[Candidate] = []

    # First scan top-level directories. If a directory is selected, do not scan its children separately.
    selected_dirs: set[Path] = set()
    for item in DATA.iterdir():
        if item.is_dir():
            reason = should_clean(item, keep_from_week=keep_from_week, cutoff=cutoff)
            if reason:
                size, files = size_of(item)
                candidates.append(Candidate(item, reason, size, files))
                selected_dirs.add(item)

    for item in DATA.rglob("*"):
        if item.is_dir():
            continue
        if any(parent in selected_dirs for parent in item.parents):
            continue
        reason = should_clean(item, keep_from_week=keep_from_week, cutoff=cutoff)
        if reason:
            size, files = size_of(item)
            candidates.append(Candidate(item, reason, size, files))

    return sorted(candidates, key=lambda candidate: candidate.size, reverse=True)


def human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.2f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def delete_candidate(candidate: Candidate) -> None:
    if not is_within_data(candidate.path):
        raise RuntimeError(f"Refuse to delete outside data directory: {candidate.path}")
    if candidate.path.is_dir():
        shutil.rmtree(candidate.path)
    elif candidate.path.exists():
        candidate.path.unlink()


def main() -> int:
    args = parse_args()
    candidates = collect_candidates(args.keep_weeks, args.keep_days)
    total_size = sum(candidate.size for candidate in candidates)
    total_files = sum(candidate.files for candidate in candidates)

    print(f"数据目录：{DATA}")
    print(f"清理模式：保留最近 {args.keep_weeks} 周；保留最近 {args.keep_days} 天的日期型缓存")
    print(f"预计清理：{len(candidates)} 项，{total_files} 个文件，约 {human_size(total_size)}")

    if not candidates:
        print("没有可清理的历史缓存。")
        return 0

    print("本次不会删除配置、CRM cookie、定时任务、更新源、学员基础名单。")
    print("清理明细 Top 20：")
    for candidate in candidates[:20]:
        relative = candidate.path.relative_to(DATA)
        print(f"- {relative} | {candidate.files} 文件 | {human_size(candidate.size)} | {candidate.reason}")
    if len(candidates) > 20:
        print(f"... 还有 {len(candidates) - 20} 项")

    if not args.apply:
        print("当前是预览模式，未删除任何文件。加 --apply 才会执行清理。")
        return 0

    deleted_files = 0
    deleted_size = 0
    for candidate in candidates:
        delete_candidate(candidate)
        deleted_files += candidate.files
        deleted_size += candidate.size

    print(f"清理完成：删除 {deleted_files} 个文件，释放约 {human_size(deleted_size)}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
