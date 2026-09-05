#!/usr/bin/env python3
"""Fetch parent chat records for many students by wrapping fetch_parent_chat_from_crm.mjs."""

from __future__ import annotations

import argparse
import csv
import io
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

WORKSPACE = Path(__file__).resolve().parents[1]
DEFAULT_SEGMENTATION = WORKSPACE / "data" / "long-term-segmentation.csv"
DEFAULT_OUT_DIR = WORKSPACE / "data" / "parent-chats"
FETCH_SCRIPT = WORKSPACE / "scripts" / "fetch_parent_chat_from_crm.mjs"


def read_ids(path: Path) -> list[str]:
    rows = list(csv.reader(path.open("r", encoding="utf-8-sig", newline="")))
    ids: list[str] = []
    for row in rows[1:]:
        if len(row) > 1 and row[1].strip():
            ids.append(row[1].strip())
    return list(dict.fromkeys(ids))


def should_fetch(user_id: str, out_dir: Path, refresh_existing: bool) -> bool:
    return refresh_existing or not (out_dir / user_id / "latest.json").exists()


def fetch_one(user_id: str, args: argparse.Namespace) -> tuple[str, int, str]:
    cmd = [
        "node",
        str(FETCH_SCRIPT),
        "--user-id",
        user_id,
        "--months",
        str(args.months),
        "--days",
        str(args.days),
        "--limit",
        str(args.limit),
        "--port",
        str(args.port),
        "--out-dir",
        str(args.out_dir),
    ]
    result = subprocess.run(
        cmd,
        cwd=str(WORKSPACE),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return user_id, result.returncode, (result.stdout or "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids-file", help="Optional file containing one userId per line")
    parser.add_argument("--segmentation-csv", default=str(DEFAULT_SEGMENTATION))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--months", type=int, default=4)
    parser.add_argument("--days", type=int, default=0, help="只抓最近 N 天的消息（优先于 --months；0 表示用 --months）")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--refresh-existing", action="store_true")
    parser.add_argument("--max-count", type=int, default=0, help="Fetch at most N students; 0 means no limit")
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--workers", type=int, default=1, help="并发抓取人数；1 表示串行")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    args.out_dir = str(out_dir)
    if args.ids_file:
        ids = [line.strip() for line in Path(args.ids_file).read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    else:
        ids = read_ids(Path(args.segmentation_csv))

    ids = [user_id for user_id in ids if should_fetch(user_id, out_dir, args.refresh_existing)]
    if args.max_count > 0:
        ids = ids[: args.max_count]

    print(f"Need fetch: {len(ids)} student(s)")
    if not ids:
        return 0

    failures: list[tuple[str, int]] = []
    workers = max(1, int(args.workers or 1))
    if workers <= 1:
        for index, user_id in enumerate(ids, 1):
            print(f"[{index}/{len(ids)}] Fetching {user_id} ...", flush=True)
            user_id, returncode, output = fetch_one(user_id, args)
            if returncode != 0:
                failures.append((user_id, returncode))
                print(f"  failed: exit {returncode}", flush=True)
                if output:
                    print(output[-1200:], flush=True)
            if args.delay > 0 and index < len(ids):
                time.sleep(args.delay)
    else:
        print(f"Using {workers} workers", flush=True)
        completed = 0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetch_one, user_id, args): user_id for user_id in ids}
            for future in as_completed(futures):
                completed += 1
                user_id, returncode, output = future.result()
                if returncode != 0:
                    failures.append((user_id, returncode))
                    print(f"[{completed}/{len(ids)}] {user_id} failed: exit {returncode}", flush=True)
                    if output:
                        print(output[-800:], flush=True)
                else:
                    print(f"[{completed}/{len(ids)}] {user_id} ok", flush=True)

    if failures:
        failed_path = out_dir / "failed-user-ids.txt"
        failed_path.write_text("\n".join(user_id for user_id, _ in failures) + "\n", encoding="utf-8")
        print(f"Failures: {len(failures)}; wrote {failed_path}")
        return 1
    print("Bulk parent chat fetch complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
