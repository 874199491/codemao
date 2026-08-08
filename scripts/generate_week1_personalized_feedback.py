#!/usr/bin/env python3
"""Generate evidence-based, student-specific week-1 parent feedback."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from teacher_workbench_config import feedback_rules_config


WORKSPACE = Path(__file__).resolve().parents[1]
INPUT_CSV = WORKSPACE / "data" / "0724-post-class-feedback.csv"
COURSE_FILES = [
    WORKSPACE / "data" / "0724-course-1-feedback.json",
    WORKSPACE / "data" / "0724-course-2-feedback.json",
]
OUTPUT_CSV = WORKSPACE / "data" / "0724-week1-personalized-feedback.csv"
WEEK_NUMBER = 1
FEEDBACK_RULES = feedback_rules_config()


def configured_options(section: str, fallback: list[str]) -> list[str]:
    templates = FEEDBACK_RULES.get("templates") if isinstance(FEEDBACK_RULES.get("templates"), dict) else {}
    value = templates.get(section) if isinstance(templates, dict) else None
    options = [str(item).strip() for item in value] if isinstance(value, list) else []
    return [item for item in options if item] or fallback


def regular_visible(rate: float | None) -> bool:
    rule = FEEDBACK_RULES.get("regular_exercise", {})
    if isinstance(rule, dict) and rule.get("enabled") is False:
        return False
    threshold = float(rule.get("mention_threshold", 80)) if isinstance(rule, dict) else 80.0
    operator = str(rule.get("threshold_operator", ">") if isinstance(rule, dict) else ">")
    if rate is None:
        return False
    return rate >= threshold if operator == ">=" else rate > threshold


def week_test_visible(rate: float | None, right: int | None = None, total: int | None = None) -> bool:
    rule = FEEDBACK_RULES.get("week_test", {})
    if isinstance(rule, dict) and rule.get("enabled") is False:
        return False
    if not isinstance(rule, dict) or rule.get("mention_only_full_score", True):
        if right is None or total is None:
            return rate == 100
        return rate == 100 and total and right == total
    threshold = float(rule.get("mention_threshold", 100))
    return rate is not None and rate >= threshold


def integer(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    try:
        return int(float(str(value).strip()))
    except ValueError:
        return None


def percent(value: str | None) -> float | None:
    if value is None or not str(value).strip():
        return None
    try:
        return float(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def load_crm_rows(
    course_files: list[Path] | None = None,
) -> dict[str, dict[int, dict[str, Any]]]:
    by_user: dict[str, dict[int, dict[str, Any]]] = {}
    for path in course_files or COURSE_FILES:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("detailRows") or []:
            uid = str(row.get("user_id") or "").strip()
            course_number = int(row.get("course_number") or 0)
            if uid and course_number > 0:
                by_user.setdefault(uid, {})[course_number] = row
    return by_user


def contact_name(student_name: str, courses: dict[int, dict[str, Any]]) -> tuple[str, str]:
    for course_number in sorted(courses, reverse=True):
        match = courses.get(course_number, {}).get("work_wechat_match_info_outbound") or {}
        child_name = str(match.get("childName") or "").strip()
        parent_name = str(match.get("parentName") or "").strip()
        if child_name:
            return child_name, f"{child_name}{parent_name or '家长'}"
    return student_name, f"{student_name}家长"


def completion_sentence(courses: dict[int, dict[str, Any]], student_id: str = "") -> tuple[str, str]:
    course_numbers = sorted(courses)
    first_course = course_numbers[0] if course_numbers else WEEK_NUMBER * 2 - 1
    second_course = course_numbers[1] if len(course_numbers) > 1 else first_course + 1
    finished = {
        number
        for number, row in courses.items()
        if bool(row.get("is_finish"))
    }
    if {first_course, second_course}.issubset(finished):
        options = configured_options("completion_finished", [
            "孩子这周两节课都已经学完了，整体学习节奏跟得上。",
            "本周两节课孩子都按时完成了，课程推进比较顺利。",
            "孩子已经完成本周两节课，整体学习进度是正常跟上的。",
            "这周两节课都有完成记录，说明孩子课后学习安排得还不错。",
            "本周课程孩子已经学完，后面主要就是把练习和错题再过一遍。",
            "孩子这周的两节课都完成了，整体节奏保持得不错。",
        ])
        seed = sum(ord(char) for char in student_id) + WEEK_NUMBER * 17
        return options[seed % len(options)], "可发送"
    if first_course in finished:
        return f"第{first_course}课已经顺利完成，第{second_course}课再抽时间接着学完，本周知识就能衔接完整了。", f"提醒补第{second_course}课"
    if second_course in finished:
        return f"第{second_course}课已经有学习记录，再按顺序回看一下第{first_course}课，知识会衔接得更顺畅。", f"提醒补第{first_course}课"
    return "孩子还在适应本周的课程节奏，建议先把两节课分段完成，不需要一次赶得太急。", "提醒补课"


def evidence_sentence(
    student_id: str,
    regular_right: int | None,
    regular_total: int | None,
    regular_rate: float | None,
    week_right: int | None,
    week_total: int | None,
    week_rate: float | None,
) -> str:
    parts: list[str] = []
    if (
        regular_visible(regular_rate)
        and regular_right is not None
        and regular_total
    ):
        label = str(FEEDBACK_RULES.get("regular_exercise", {}).get("label", "课中习题"))
        parts.append(f"{label}正确率{regular_rate:g}%")
    if (
        week_test_visible(week_rate, week_right, week_total)
    ):
        parts.append(str(FEEDBACK_RULES.get("week_test", {}).get("full_score_text", "周测100%正确")))
    if not parts:
        return ""
    result = "，".join(parts)
    options = configured_options("evidence", [
        f"孩子这周的答题情况是：{result}。",
        f"具体看了一下，孩子{result}。",
        f"这周孩子{result}，整体完成情况比较清楚。",
        f"我看了下孩子的练习情况，{result}。",
        f"从本周的数据来看，孩子{result}。",
        f"孩子本周已经完成相应练习，其中{result}。",
        f"再看具体的完成结果，孩子{result}。",
        f"这周的练习数据里，孩子{result}。",
    ])
    seed = sum(ord(char) for char in student_id) + WEEK_NUMBER * 7
    template = options[seed % len(options)]
    return template.format(result=result)


def performance_sentence(
    regular_rate: float | None,
    week_rate: float | None,
    student_id: str = "",
) -> str:
    has_regular = regular_visible(regular_rate)
    has_week = week_test_visible(week_rate)
    if has_regular and has_week:
        options = configured_options("performance_high", [
            "这周整体状态很好，课堂内容吸收得也不错。",
            "这周学习状态很在线，关键内容基本都跟上了。",
            "这周完成质量很高，说明孩子上课和练习都有认真跟进。",
            "这周的表现挺亮眼，说明相关知识点已经掌握得不错。",
            "这周不管是练习还是周测都完成得很好，继续保持这个节奏。",
        ])
        seed = sum(ord(char) for char in student_id) + WEEK_NUMBER * 13
        return options[seed % len(options)]
    if has_week:
        return "复习后的掌握情况很不错。"
    if has_regular:
        return "课堂内容跟得比较稳。"
    return ""


def advice_sentence(
    regular_rate: float | None,
    week_rate: float | None,
    has_week_test: bool,
    grade: str = "",
) -> str:
    top_grade = str(FEEDBACK_RULES.get("rating", {}).get("top", "S"))
    if grade == top_grade:
        return "这周整体掌握得比较到位，下周继续保持现在的学习节奏就很好。"
    if regular_visible(regular_rate):
        return "建议下周上课前花5分钟回看一下课堂示例，把现在的学习节奏延续下去。"
    if week_test_visible(week_rate):
        return "建议下周上课前简单回顾一下本周知识点，把现在的学习状态保持住。"
    if WEEK_NUMBER == 1:
        return "建议再回顾一下单双引号、注释格式和cout输出写法，把基础细节熟悉好。"
    return "建议下周上课前花5分钟回看一下本周错题和课堂示例，把现在的学习节奏延续下去。"


def consolidation_sentence(
    courses: dict[int, dict[str, Any]],
    regular_rate: float | None,
    week_rate: float | None,
    has_week_test: bool,
    combined_rate: float | None,
    grade: str,
) -> str:
    rating = FEEDBACK_RULES.get("rating", {})
    base_grade = str(rating.get("base", "A") if isinstance(rating, dict) else "A")
    base_max = float(
        rating.get("base_max_combined_rate", 79) if isinstance(rating, dict) else 79
    )
    if grade != base_grade:
        return ""
    if combined_rate is not None and combined_rate > base_max:
        return ""

    topic = course_topic_text(courses)
    weak_points: list[str] = []
    if not regular_visible(regular_rate):
        weak_points.append("课中习题里暴露出来的基础写法和解题步骤")
    if not has_week_test:
        weak_points.append("本周周测对应的知识点")
    elif not week_test_visible(week_rate):
        weak_points.append("周测中的易错题和细节判断")
    if not weak_points:
        weak_points.append("课堂例题和课后错题")

    details = "、".join(weak_points[:2])
    options = [
        f"这周建议重点巩固{topic}，尤其是{details}，先把容易出错的地方重新过一遍。",
        f"后面可以再针对{topic}做一点复习，重点看{details}，把基础细节再压实一些。",
        f"目前需要再巩固的主要是{topic}相关内容，特别是{details}，不需要赶进度，先把这部分弄稳。",
        f"建议接下来把{topic}里的关键规则再梳理一下，{details}可以多练几题，熟练度会更好。",
    ]
    seed = sum(ord(char) for char in topic) + WEEK_NUMBER * 11
    return options[seed % len(options)]


def note_praise(student_id: str, week_number: int) -> str:
    options = configured_options("note_praise", [
        "另外，孩子这周的课程笔记也有及时上传，学习过程跟得比较认真～",
        "这周孩子还把课程笔记提交了，边学边整理的习惯很不错。",
        "笔记部分也完成了，及时把学到的内容整理下来，这一点值得继续保持。",
        "我也看到孩子上传了本周笔记，说明课后的整理有跟上。",
        "孩子这周的笔记也交上来了，整体学习节奏不错。",
        "本周笔记已经提交，愿意把知识记录下来，是一个很好的学习习惯。",
        "孩子这周有认真完成课程笔记，之后回顾和复习也会更方便。",
        "课程笔记也按要求完成了，这种及时整理的习惯挺好的。",
    ])
    seed = sum(ord(char) for char in student_id) + week_number * 3
    return options[seed % len(options)]


def feedback_opening(student_id: str, week_number: int) -> str:
    salutation = "#{#学生昵称}#{#家长称谓}"
    options = configured_options("openings", [
        f"{salutation}，我这边给您反馈一下孩子本周的学习表现。",
        f"{salutation}，和您同步一下孩子这周的课程表现。",
        f"{salutation}，我刚看完孩子本周的课程记录，和您说一下整体情况。",
        f"{salutation}，孩子本周的课程情况我已经看过了，和您简单反馈一下。",
        f"{salutation}，我整理了一下孩子这周的学习情况，和您同步一下。",
        f"{salutation}，这周的课程数据我看过了，给您说一下孩子的具体表现。",
        f"{salutation}，我结合孩子本周的课程和作答情况，给您做个反馈。",
        f"{salutation}，和您说一下孩子这一周的课程完成和学习情况。",
    ])
    seed = sum(ord(char) for char in student_id) + week_number * 5
    return options[seed % len(options)]


def build_feedback(
    row: dict[str, str],
    courses: dict[int, dict[str, Any]],
) -> tuple[str, str, str]:
    regular_right = integer(row.get("课中习题正确数"))
    regular_total = integer(row.get("课中习题总数"))
    week_right = integer(row.get("周测正确数"))
    week_total = integer(row.get("周测题目数"))
    regular_rate = percent(row.get("课中习题正确率"))
    week_rate = percent(row.get("周测正确率"))
    combined_rate = percent(row.get("综合正确率"))
    rating_rule = FEEDBACK_RULES.get("rating", {})
    rating_enabled = not isinstance(rating_rule, dict) or rating_rule.get("enabled", True)
    base_grade = str(rating_rule.get("base", "A") if isinstance(rating_rule, dict) else "A")
    excellent_grade = str(rating_rule.get("excellent", "A+") if isinstance(rating_rule, dict) else "A+")
    top_grade = str(rating_rule.get("top", "S") if isinstance(rating_rule, dict) else "S")
    excellent_min = float(
        rating_rule.get("excellent_min_combined_rate", 80) if isinstance(rating_rule, dict) else 80
    )
    top_min = float(
        rating_rule.get("top_min_combined_rate", 95) if isinstance(rating_rule, dict) else 95
    )
    excellent_requires_week_test = (
        bool(rating_rule.get("excellent_requires_week_test", True))
        if isinstance(rating_rule, dict)
        else True
    )
    top_requires_week_test_full_score = (
        bool(rating_rule.get("top_requires_week_test_full_score", True))
        if isinstance(rating_rule, dict)
        else True
    )
    grade = base_grade
    if rating_enabled and combined_rate is not None and combined_rate >= excellent_min:
        if not excellent_requires_week_test or week_rate is not None:
            grade = excellent_grade
    if rating_enabled and combined_rate is not None and combined_rate >= top_min:
        if not top_requires_week_test_full_score or week_test_visible(week_rate, week_right, week_total):
            grade = top_grade
    note_submitted = row.get("是否完成笔记", "").strip() == "是"

    completion, action = completion_sentence(courses, row["学生ID"])
    evidence = evidence_sentence(
        row["学生ID"],
        regular_right,
        regular_total,
        regular_rate,
        week_right,
        week_total,
        week_rate,
    )
    performance = performance_sentence(regular_rate, week_rate, row["学生ID"])
    advice = advice_sentence(regular_rate, week_rate, week_total is not None, grade)
    consolidation = consolidation_sentence(
        courses,
        regular_rate,
        week_rate,
        week_total is not None,
        combined_rate,
        grade,
    )

    closings = configured_options("closings", [
        "刚开始学C++，先把习惯和基础打好最重要，后面有问题随时找我哈～",
        "下周我也会继续留意孩子的状态，咱们一起把基础慢慢打扎实。",
        "第一周先把课程节奏跟稳，继续保持现在的学习状态就好。",
    ])
    variant_index = sum(ord(char) for char in row["学生ID"]) % len(closings)
    opening = feedback_opening(row["学生ID"], WEEK_NUMBER)
    closing = closings[variant_index]

    feedback_before_contact = (
        f"{opening}\n\n"
        f"{completion}\n\n"
        + (f"{evidence}{performance}\n\n" if evidence else "")
        + f"这周主要学习了{course_topic_text(courses)}。"
        + (f"{consolidation}" if consolidation else f"{advice}")
        + (
            "\n\n" + note_praise(row["学生ID"], WEEK_NUMBER)
            if note_submitted
            and FEEDBACK_RULES.get("notes", {}).get("enabled", True)
            and FEEDBACK_RULES.get("notes", {}).get("mention_if_submitted", True)
            else ""
        )
        + "\n\n"
        + f"{closing}\n\n"
        + (
            str(rating_rule.get("line_template", "本周综合评级：{grade}")).format(grade=grade)
            if rating_enabled
            else ""
        )
    )
    contact_rule = FEEDBACK_RULES.get("contact", {})
    contact_rule = contact_rule if isinstance(contact_rule, dict) else {}
    contact_text = str(contact_rule.get("text", "有什么问题您随时联系我哈～") if isinstance(contact_rule, dict) else "有什么问题您随时联系我哈～").strip()
    dedupe_keywords = (
        contact_rule.get("dedupe_keywords", [])
        if isinstance(contact_rule, dict)
        else []
    )
    has_earlier_contact = any(
        str(keyword).strip() and str(keyword).strip() in feedback_before_contact
        for keyword in dedupe_keywords
    )
    feedback = feedback_before_contact + (
        ""
        if not contact_rule.get("enabled", True) or not contact_text or has_earlier_contact
        else f"\n\n{contact_text}"
    )
    return feedback, action, grade


def course_topic_text(courses: dict[int, dict[str, Any]]) -> str:
    names: list[str] = []
    for course_number in sorted(courses):
        raw = str(courses[course_number].get("course_name") or "").strip()
        name = raw.split("-", 1)[-1].strip() if "-" in raw else raw
        if name and name not in names:
            names.append(name)
    return "、".join(names) if names else "本周课程内容"


def main() -> int:
    global WEEK_NUMBER
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pending-only",
        action="store_true",
        help="Only generate rows whose 是否已反馈 is false.",
    )
    parser.add_argument(
        "--include-missing-week-test",
        action="store_true",
        help="Also include students who finished courses 1-2 but have no week-test result.",
    )
    parser.add_argument(
        "--finished-only",
        action="store_true",
        help="Only include students who finished both week-1 courses.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_CSV,
        help="Output CSV path.",
    )
    parser.add_argument("--week", type=int, default=1)
    parser.add_argument("--input", type=Path, default=INPUT_CSV)
    parser.add_argument("--course-files", type=Path, nargs=2)
    args = parser.parse_args()
    WEEK_NUMBER = args.week
    first_course_number = args.week * 2 - 1
    second_course_number = first_course_number + 1

    crm_rows = load_crm_rows(args.course_files)
    with args.input.open("r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))

    output_rows: list[dict[str, str]] = []
    for row in rows:
        uid = row["学生ID"].strip()
        courses = crm_rows.get(uid, {})
        both_courses_finished = bool(
            courses.get(first_course_number, {}).get("is_finish")
        ) and bool(
            courses.get(second_course_number, {}).get("is_finish")
        )
        if args.finished_only and not both_courses_finished:
            continue
        feedback_done = str(row.get("是否已反馈") or "").strip().upper() in {
            "TRUE",
            "1",
            "YES",
            "是",
        }
        if args.pending_only and feedback_done:
            continue
        has_week_test = integer(row.get("周测题目数")) is not None
        if not has_week_test:
            if not args.include_missing_week_test or not both_courses_finished:
                continue
        feedback, action, grade = build_feedback(row, courses)
        output_rows.append(
            {
                "学生ID": uid,
                "学生姓名": row["学生姓名"],
                "上课时间": row["上课时间"],
                "课中习题": (
                    f"{row['课中习题正确数']}/{row['课中习题总数']}"
                    if row.get("课中习题总数")
                    else "暂无"
                ),
                "周测": (
                    f"{row['周测正确数']}/{row['周测题目数']}"
                    if row.get("周测题目数")
                    else "暂无"
                ),
                "课程表现": grade,
                "是否完成笔记": row.get("是否完成笔记", ""),
                "建议操作": action,
                "个性化反馈话术": feedback,
                "人工复核": "FALSE",
            }
        )

    headers = [
        "学生ID",
        "学生姓名",
        "上课时间",
        "课中习题",
        "周测",
        "课程表现",
        "是否完成笔记",
        "建议操作",
        "个性化反馈话术",
        "人工复核",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=headers)
        writer.writeheader()
        writer.writerows(output_rows)

    summary = {
        "output": str(args.output),
        "students": len(output_rows),
        "with_regular_data": sum(row["课中习题"] != "暂无" for row in output_rows),
        "with_week_test": sum(row["周测"] != "暂无" for row in output_rows),
        "a_plus": sum(row["课程表现"] == "A+" for row in output_rows),
        "a": sum(row["课程表现"] == "A" for row in output_rows),
        "needs_completion_followup": sum(
            row["建议操作"] != "可发送" for row in output_rows
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
