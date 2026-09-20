#!/usr/bin/env python3
"""Generate combined weak-point reports for the configured teacher's selected week.

For a given week, this script:
  1. resolves the week's two lessons (course_numbers) via week_context,
  2. pulls each lesson's course detail from CRM to get the real course_id and the
     set of students who finished it (is_finish),
  3. keeps students who finished BOTH lessons,
  4. concurrently fetches each student's question detail for both lessons,
  5. renders one combined "knowledge-point + wrong-question" report per student,
     saved under data/错题报告-week{N}/ as 《姓名_第N周错题解析.pdf》.

Usage:
  python generate_week_weak_reports.py --week <N> [--concurrency 8]
"""
from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from pathlib import Path

W = Path(__file__).resolve().parents[1]
DATA = W / "data"
SCRIPTS = W / "scripts"
FETCH_DETAIL = SCRIPTS / "fetch_course_detail_from_crm.mjs"
FETCH_ONE = SCRIPTS / "fetch_single_student_questions.mjs"
GEN = SCRIPTS / "generate_weak_point_report.py"
PORT = "9223"

sys.path.insert(0, str(SCRIPTS))
from teacher_workbench_config import data_path, data_prefix, script_config  # noqa: E402

CONFIG = script_config()
PREFIX = data_prefix(CONFIG)
CLASS_FILE = data_path("completion_classes_csv", CONFIG)
CURRENT_WEEK_JSON = DATA / f"{PREFIX}-latest-week-context.json"


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



def collect_week_knowledge_labels(uids: list[str], course_ids: list[int], qd_dir: Path) -> list[str]:
    sys.path.insert(0, str(SCRIPTS))
    from generate_weak_point_report import classify, load_student, should_exclude_report_question  # noqa: E402

    labels = []
    seen = set()
    for uid in sorted(uids):
        for cid in course_ids:
            path = qd_dir / f"{uid}_{cid}.json"
            if not path.is_file():
                continue
            try:
                items = load_student([path])
            except Exception:
                continue
            for q in items:
                status, labs = classify(q)
                if status != "wrong":
                    continue
                try:
                    q_type = int(q.get("type") or 0)
                except Exception:
                    q_type = 0
                if q_type == 3 or should_exclude_report_question(q):
                    continue
                for lab in labs or ["未知"]:
                    lab = str(lab or "").strip()
                    if lab and lab not in seen:
                        seen.add(lab)
                        labels.append(lab)
    return labels



def extract_user_id(row: object) -> str:
    if not isinstance(row, dict):
        return ""
    nested = row.get("student")
    if isinstance(nested, dict):
        value = extract_user_id(nested)
        if value:
            return value
    for key in ("user_id", "userId", "student_id", "studentId", "用户ID", "学员ID", "学生ID"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def extract_student_name(row: object) -> str:
    if not isinstance(row, dict):
        return ""
    nested = row.get("student")
    if isinstance(nested, dict):
        value = extract_student_name(nested)
        if value:
            return value
    for key in ("child_name", "childName", "student_name", "studentName", "name", "学生姓名", "学员姓名", "孩子姓名"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def iter_json_rows(payload: object):
    if isinstance(payload, list):
        yield from payload
        return
    if not isinstance(payload, dict):
        return
    for key in ("detailRows", "rows", "students", "items", "data", "list"):
        value = payload.get(key)
        if isinstance(value, list):
            yield from value
        elif isinstance(value, dict):
            yield from iter_json_rows(value)
    for key in ("classStudents", "studentList", "records"):
        value = payload.get(key)
        if isinstance(value, list):
            yield from value


def add_names_from_json(name_by_uid: dict[str, str], path: Path) -> None:
    if not path.is_file():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    for row in iter_json_rows(payload):
        uid = extract_user_id(row)
        name = extract_student_name(row)
        if uid and name:
            name_by_uid.setdefault(uid, name)


def load_name_map() -> dict[str, str]:
    name_by_uid: dict[str, str] = {}
    for key in ("students_json",):
        try:
            add_names_from_json(name_by_uid, data_path(key, CONFIG))
        except Exception:
            pass
    for pattern in (
        f"{PREFIX}-student-completion-detail.json",
        "new-class-student-list.json",
        f"{PREFIX}-course-*-feedback.json",
        f"{PREFIX}-probe-*.json",
    ):
        for path in sorted(DATA.glob(pattern)):
            add_names_from_json(name_by_uid, path)
    return name_by_uid

def resolve_lesson_course_id(course_number: int) -> tuple[int, list[str]]:
    """Return (course_id, finished_user_ids) from the current teacher's CRM classes."""
    probe = DATA / f"{PREFIX}-probe-{course_number}.json"
    cmd = [
        "node",
        str(FETCH_DETAIL),
        "--course-num",
        str(course_number),
        "--course-id",
        "0",
        "--port",
        PORT,
        "--out-json",
        str(probe),
    ]
    if CLASS_FILE.is_file():
        cmd.extend(["--class-file", str(CLASS_FILE)])
    r = run(cmd, timeout=120)
    if r.returncode != 0 or not probe.is_file():
        raise RuntimeError(f"拉取课程明细失败（course_number={course_number}）：\n" + (r.stdout + r.stderr)[-1500:])
    payload = json.loads(probe.read_text(encoding="utf-8"))
    rows = payload.get("detailRows") or []
    target = [row for row in rows if str(row.get("course_number")) == str(course_number)]
    if not target:
        raise RuntimeError(f"课程明细中没有 course_number={course_number} 的记录")
    course_id = int(target[0]["course_id"])
    finished = [str(row["user_id"]) for row in target if row.get("is_finish") and row.get("user_id")]
    return course_id, sorted(set(finished))

def safe_filename_part(value: str) -> str:
    text = str(value or "").strip() or "未命名"
    for ch in '<>:"/\\|?*':
        text = text.replace(ch, '_')
    return text.rstrip(' .') or "未命名"


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
    parser.add_argument("--concurrency", type=int, default=8, help="抓取题目明细的并发数")
    parser.add_argument("--render-concurrency", type=int, default=10, help="生成 PDF / AI 解析的并发数，默认 10")
    parser.add_argument("--student-json-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--knowledge-json", type=Path, default=None, help="统一知识点讲解 JSON 路径；不传则使用默认 week 文件")
    parser.add_argument("--limit", type=int, default=0, help="只生成前 N 个学员，用于小批量测试")
    args = parser.parse_args()

    weeks = current_courses(args.week)
    print("本周课时 numbers:", weeks, flush=True)

    lesson_map = {}
    finished_sets = []
    for cn in weeks:
        cid, finished = resolve_lesson_course_id(cn)
        lesson_map[cn] = cid
        finished_sets.append(set(finished))
        print(f"  课时 {cn} -> course_id {cid}，已完课 {len(finished)} 人", flush=True)

    name_by_uid = load_name_map()

    both = (finished_sets[0] & finished_sets[1]) if len(finished_sets) > 1 else finished_sets[0]
    print("两课均完课学员:", len(both), flush=True)
    missing_names = sum(1 for uid in both if not name_by_uid.get(uid))
    if missing_names:
        print(f"姓名映射缺失 {missing_names} 人，将临时使用学生ID命名。", flush=True)
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

    week_labels = collect_week_knowledge_labels(list(both), course_ids, qd_dir)
    knowledge_json = args.knowledge_json or (DATA / f"错题报告-week{args.week}-knowledge.json")
    if week_labels:
        print("本周统一知识点:", "、".join(week_labels), flush=True)
    else:
        print("本周未识别到错题知识点。", flush=True)

    out_dir = args.out_dir or (DATA / f"错题报告-week{args.week}")
    out_dir.mkdir(parents=True, exist_ok=True)
    title = f"第{args.week}周"
    ok = skipped = existing = 0
    errors = []

    display_name_counts = Counter(safe_filename_part(name_by_uid.get(uid, uid)) for uid in both)

    def render_one(uid: str):
        jsons = [qd_dir / f"{uid}_{cid}.json" for cid in course_ids]
        if not all(j.is_file() for j in jsons):
            return "skipped", uid, "缺题目数据"
        sname = name_by_uid.get(uid, uid)
        display_name = safe_filename_part(sname)
        suffix = f"_{uid}" if display_name_counts.get(display_name, 0) > 1 else ""
        out = out_dir / f"{display_name}_第{args.week}周错题解析{suffix}.pdf"
        if out.is_file():
            return "existing", uid, ""
        cmd = [sys.executable, str(GEN),
               "--student-json", str(jsons[0]), "--student-json", str(jsons[1]),
               "--name", sname, "--course-title", title, "--out", str(out),
               "--knowledge-json", str(knowledge_json)]
        for lab in week_labels:
            cmd.extend(["--knowledge-label", lab])
        r = run(cmd, timeout=300)
        if r.returncode == 0 and out.is_file():
            return "ok", uid, ""
        if r.returncode == 2:
            return "skipped", uid, (r.stderr or r.stdout).strip()[-200:]
        return "error", uid, (r.stderr or r.stdout).strip()[-300:]

    render_workers = max(1, int(args.render_concurrency or 1))
    print(f"并发生成 PDF/AI 解析：{render_workers} 个学员同时处理…", flush=True)
    render_uids = sorted(both)
    if args.limit and args.limit > 0:
        render_uids = render_uids[:args.limit]
        print(f"本次仅生成前 {len(render_uids)} 个学员。", flush=True)
    with ThreadPoolExecutor(max_workers=render_workers) as ex:
        futures = [ex.submit(render_one, uid) for uid in render_uids]
        done_count = 0
        for fut in as_completed(futures):
            status, uid, msg = fut.result()
            done_count += 1
            if status == "ok":
                ok += 1
            elif status == "existing":
                existing += 1
            elif status == "skipped":
                skipped += 1
            else:
                errors.append((uid, msg))
            if done_count % 10 == 0 or done_count == len(futures):
                print(f"生成进度 {done_count}/{len(futures)}：新增 {ok}，已有 {existing}，跳过 {skipped}，错误 {len(errors)}", flush=True)
    print(f"新增生成 {ok} 份，已有跳过 {existing} 份，缺数据跳过 {skipped} 份，错误 {len(errors)} 份。", flush=True)
    for e in errors[:10]:
        print("ERR", e, flush=True)
    print("输出目录:", out_dir, flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())



