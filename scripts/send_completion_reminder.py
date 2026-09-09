#!/usr/bin/env python3
"""Send a reviewed completion reminder through the existing CRM pending-task channel."""
import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from teacher_workbench_config import load_workbench_config, script_config, data_path

ROOT = Path(__file__).resolve().parents[1]


def run(manifest_path, execute=False):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    students = manifest["students"]
    if not students or not manifest["message"].strip():
        raise ValueError("提醒名单和正文不能为空")
    folder = manifest_path.parent
    rows_path = folder / "recipients.csv"
    with rows_path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=["学生ID", "学生姓名", "上课时间", "个性化反馈话术"])
        writer.writeheader()
        for student in students:
            writer.writerow({"学生ID": student["id"], "学生姓名": student.get("name", ""), "上课时间": student.get("class_time", ""), "个性化反馈话术": manifest["message"]})
    if not execute:
        print(f"仅准备提醒：{len(students)} 人；未请求 CRM，未创建群发任务。", flush=True)
        return
    config = load_workbench_config()
    course_ids = []
    for course_num in manifest["courses"]:
        course_path = folder / f"course-{course_num}.json"
        subprocess.run(["node", str(ROOT / "scripts/fetch_course_detail_from_crm.mjs"), "--port", str(config.get("chrome_debug_port") or 9223), "--course-num", str(course_num), "--course-id", "0", "--class-file", str(data_path("completion_classes_csv", script_config())), "--out-json", str(course_path)], check=True, cwd=ROOT)
        course = json.loads(course_path.read_text(encoding="utf-8"))
        course_id = int(course.get("courseId") or 0)
        if course_id <= 0 or int(course.get("detailCount") or 0) <= 0:
            raise ValueError("无法确认实际课程 ID，未创建群发任务")
        course_ids.append(course_id)
    if not course_ids:
        raise ValueError("没有可用课程，未创建群发任务")
    print("正在创建未完课提醒任务；不会修改课后反馈勾选。", flush=True)
    subprocess.run([sys.executable, str(ROOT / "scripts/send_week1_personalized_feedback.py"), "--input", str(rows_path), "--week", str(manifest["week"]), "--course-id", str(course_ids[-1]), "--unlock-course-ids", *map(str, course_ids), "--result", str(folder / "result.json"), "--execute"], check=True, cwd=ROOT)
    print("提醒任务处理结束，逐人结果见 result.json；最终发送需在企业微信确认。", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    run(args.manifest, args.execute)
