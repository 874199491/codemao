#!/usr/bin/env python3
"""One-shot pipeline: fetch one student's question detail and build a weak-point PDF.

Usage:
  python build_student_weak_report.py --user-id 624712975 --course-id 9346 \
      --name 潘晓宇 --course-title "第13课 12-char 和 bool" --out <pdf>

Uses fetch_single_student_questions.mjs to pull the CRM question detail for a single
student (fast, one request), then generate_weak_point_report.py to render the PDF.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
FETCH = WORKSPACE / "scripts" / "fetch_single_student_questions.mjs"
GEN = WORKSPACE / "scripts" / "generate_weak_point_report.py"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--course-id", type=int, required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--course-title", default="")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--port", type=int, default=9223)
    parser.add_argument("--keep-json", type=Path, default=None, help="保存中间错题 JSON（默认临时）")
    args = parser.parse_args()

    if not FETCH.is_file():
        print(f"缺少抓取脚本：{FETCH}", file=sys.stderr)
        return 1

    tmp = args.keep_json or (WORKSPACE / "data" / f"student-{args.user_id}-questions.json")
    tmp = tmp.resolve()
    tmp.parent.mkdir(parents=True, exist_ok=True)

    fetch_cmd = [
        "node", str(FETCH), "--user-id", args.user_id, "--course-id", str(args.course_id),
        "--port", str(args.port), "--out-json", str(tmp),
    ]
    print("抓取题目明细：", " ".join(fetch_cmd), flush=True)
    result = subprocess.run(fetch_cmd, cwd=str(WORKSPACE), capture_output=True, text=True)
    if result.returncode != 0:
        print("抓取失败：\n" + (result.stdout + result.stderr)[-2000:], file=sys.stderr)
        return 1
    if not tmp.is_file():
        print("抓取后未生成 JSON 文件", file=sys.stderr)
        return 1
    print("已抓取：", tmp, flush=True)

    gen_cmd = [
        sys.executable, str(GEN),
        "--student-json", str(tmp),
        "--name", args.name,
        "--course-title", args.course_title,
        "--out", str(args.out),
    ]
    print("生成报告：", " ".join(gen_cmd), flush=True)
    result = subprocess.run(gen_cmd, cwd=str(WORKSPACE), capture_output=True, text=True)
    if result.returncode != 0:
        print("生成失败：\n" + (result.stdout + result.stderr)[-3000:], file=sys.stderr)
        return 1
    print("生成成功：", args.out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
