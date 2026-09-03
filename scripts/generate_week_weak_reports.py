#!/usr/bin/env python3
"""Generate combined weak-point reports for a selected 0724 week's finished students.

For a given week, this script:
  1. resolves the week's two lessons (course_numbers) via week_context,
  2. pulls each lesson's course detail from CRM to get the real course_id and the
     set of students who finished it (is_finish),
  3. keeps students who finished BOTH lessons,
  4. concurrently fetches each student's question detail for both lessons,
  5. renders one combined "knowledge-point + wrong-question" report per student,
     saved under data/错题报告-week{N}/ as 《姓名_id.pdf》.

Usage:
  python generate_week_weak_reports.py --week <N> [--concurrency 8]
"""
from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

W = Path(__file__).resolve().parents[1]
DATA = W / "data"
SCRIPTS = W / "scripts"
FETCH_DETAIL = SCRIPTS / "fetch_course_detail_from_crm.mjs"
FETCH_ONE = SCRIPTS / "fetch_single_student_questions.mjs"
GEN = SCRIPTS / "generate_weak_point_report.py"
CURRENT_WEEK_JSON = DATA / "0724-latest-week-context.json"
PORT = "9223"


def current_courses(week: int) -> list[int]:
    """Return the week's two lesson course_numbers."""
    if CURRENT_WEEK_JSON.is_file():
        ctx = json.loads(CURRENT_WEEK_JSON.read_text(encoding="utf-8"))
        if int(ctx.get("week", 0)) == week:
            courses = ctx.get("courses") or []
            if courses:
                return [int(c) for c in courses]
    # fallback: compute from week_context
    sys.path.insert(0, str(SCRIPTS))
    from week_context import course_numbers_for_week  # noqa: E402

    a, b = course_numbers_for_week(week)
    return [a, b]


def run(cmd, timeout=600):
    return subprocess.run(
        cmd, cwd=str(W), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def resolve_lesson_course_id(course_number: int) -> tuple[int, list[str]]:
    """Pull course detail for a lesson, return (course_id, finished_user_ids)."""
    probe = DATA / f"probe-{course_number}.json"
    r = run(["node", str(FETCH_DETAIL), "--course-num", str(course_number), "--course-id", "0",
             "--port", PORT, "--current-0724", "--out-json", str(probe)])
    if r.returncode != 0 or not probe.is_file():
        raise RuntimeError(f"拉取课程明细失败（course_number={course_number}）：\n" + r.stdout[-1500:])
    payload = json.loads(probe.read_text(encoding="utf-8"))
    rows = payload.get("detailRows") or []
    target = [row for row in rows if str(row.get("course_number")) == str(course_number)]
    if not target:
        raise RuntimeError(f"课程明细中没有 course_number={course_number} 的记录")
    course_id = int(target[0]["course_id"])
    finished = [str(row["user_id"]) for row in target if row.get("is_finish") and row.get("user_id")]
    return course_id, sorted(set(finished))


def fetch_one_student(uid: str, course_ids, qd_dir: Path):
    for cid in course_ids:
        out = qd_dir / f"{uid}_{cid}.json"
        if out.is_file():
            continue
        run(["node", str(FETCH_ONE), "--user-id", uid, "--course-id", str(cid),
             "--port", PORT, "--out-json", str(out)], timeout=90)


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--student-json-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()

    name_by_uid = {}
    # read name map from course-12 feedback if available
    if (DATA / "0724-course-12-feedback.json").is_file():
        d = json.loads((DATA / "0724-course-12-feedback.json").read_text(encoding="utf-8"))
        for row in d.get("detailRows") or []:
            if row.get("user_id"):
                name_by_uid.setdefault(str(row["user_id"]), row.get("child_name", ""))

    weeks = current_courses(args.week)
    print("本周课时 numbers:", weeks, flush=True)

    lesson_map = {}
    finished_sets = []
    for cn in weeks:
        cid, finished = resolve_lesson_course_id(cn)
        lesson_map[cn] = cid
        finished_sets.append(set(finished))
        print(f"  课时 {cn} -> course_id {cid}，已完课 {len(finished)} 人", flush=True)

    both = (finished_sets[0] & finished_sets[1]) if len(finished_sets) > 1 else finished_sets[0]
    print("两课均完课学员:", len(both), flush=True)
    if not both:
        print("该周无两课均完课学员，未生成报告。", flush=True)
        return 0

    qd_dir = args.student_json_dir or (DATA / f"week{args.week}-qd")
    qd_dir.mkdir(parents=True, exist_ok=True)
    course_ids = [lesson_map[cn] for cn in weeks]

    # fetch questions concurrently
    work = list(both)
    print(f"并发抓取 {len(work)} 名学员 × {len(course_ids)} 课题目…", flush=True)
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        for _ in as_completed([ex.submit(fetch_one_student, uid, course_ids, qd_dir) for uid in work]):
            pass
    print("题目抓取完成。", flush=True)

    out_dir = args.out_dir or (DATA / f"错题报告-week{args.week}")
    out_dir.mkdir(parents=True, exist_ok=True)
    title = f"第{args.week}周"
    ok = skipped = existing = 0
    errors = []
    for uid in sorted(both):
        jsons = [qd_dir / f"{uid}_{cid}.json" for cid in course_ids]
        if not all(j.is_file() for j in jsons):
            skipped += 1
            continue
        sname = name_by_uid.get(uid, uid)
        out = out_dir / f"{sname}_{uid}.pdf"
        if out.is_file():
            existing += 1
            continue  # 增量：已有报告不再重新生成
        r = run([sys.executable, str(GEN),
                 "--student-json", str(jsons[0]), "--student-json", str(jsons[1]),
                 "--name", sname, "--course-title", title, "--out", str(out)], timeout=120)
        if r.returncode == 0 and out.is_file():
            ok += 1
        elif r.returncode == 2:
            skipped += 1
        else:
            errors.append((uid, r.stderr.strip()[-200:]))
    print(f"新增生成 {ok} 份，已有跳过 {existing} 份，缺数据跳过 {skipped} 份，错误 {len(errors)} 份。", flush=True)
    for e in errors[:10]:
        print("ERR", e, flush=True)
    print("输出目录:", out_dir, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
