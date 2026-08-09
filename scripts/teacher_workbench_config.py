#!/usr/bin/env python3
"""Shared configuration for the distributable CodeMao teacher workbench."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[1]
CONFIG_PATH = WORKSPACE / "data" / "teacher-workbench-config.json"

DEFAULT_PROFILE: dict[str, Any] = {
    "data_prefix": "0724",
    "dingtalk": {
        "node_id": "N7dx2rn0JbZQBadbCZjmZM42JMGjLRb3",
        "learning_sheet_id": "st-f09de483-168637",
        "learning_sheet_range": "A1:AZ300",
        "invite_followup_sheet_name": "邀约跟进",
        "makeup_sheet_name": "补课表",
    },
    "classes": [
        {"class_id": 130020, "label": "周五晚", "match_prefix": "周五"},
        {"class_id": 130021, "label": "周六午", "match_prefix": "周六午"},
        {"class_id": 130022, "label": "周六晚", "match_prefix": "周六晚"},
    ],
    "files": {
        "completion_classes_csv": "data/0724-completion-classes-20260724.csv",
        "students_json": "data/group-student-completion-detail.json",
        "roster_csv": "data/new-class-student-questionnaire-selected-columns.csv",
        "refunded_json": "data/new-class-refunded-students.json",
        "confirmed_refunded_json": "data/0724-refunded-students.json",
    },
    "wecom": {
        "enabled": True,
        "chat_id_source": "crm_capture",
        "chat_id_cache": "data/0724-wecom-parent-chat-ids.json",
        "send_result_pattern": "data/0724-week{week}-feedback-send-result.json",
        "require_preview_before_send": True,
        "mark_feedback_after_confirmed_send": True,
    },
}

DEFAULT_FEEDBACK_RULES: dict[str, Any] = {
    "regular_exercise": {
        "enabled": True,
        "label": "课中习题",
        "mention_threshold": 80,
        "threshold_operator": ">",
    },
    "week_test": {
        "enabled": True,
        "mention_only_full_score": True,
        "full_score_text": "周测100%正确",
        "remind_if_missing": True,
    },
    "notes": {
        "enabled": True,
        "mention_if_submitted": True,
    },
    "homework_correction": {
        "enabled": True,
        "text": "课后作业里有错题的话，建议课后再抽一点时间完成订正，把出错的地方重新过一遍。",
    },
    "rating": {
        "enabled": True,
        "base": "A",
        "excellent": "A+",
        "top": "S",
        "base_max_combined_rate": 79,
        "excellent_min_combined_rate": 80,
        "excellent_requires_week_test": True,
        "top_min_combined_rate": 95,
        "top_requires_week_test_full_score": True,
        "line_template": "本周综合评级：{grade}",
    },
    "contact": {
        "enabled": True,
        "text": "有什么问题您随时联系我哈～",
        "dedupe_keywords": ["有问题随时找我", "有问题随时联系我", "随时联系我"],
    },
    "templates": {
        "openings": [
            "#{#学生昵称}#{#家长称谓}，我这边给您反馈一下孩子本周的学习表现。",
            "#{#学生昵称}#{#家长称谓}，和您同步一下孩子这周的课程表现。",
            "#{#学生昵称}#{#家长称谓}，我刚看完孩子本周的课程记录，和您说一下整体情况。",
            "#{#学生昵称}#{#家长称谓}，孩子本周的课程情况我已经看过了，和您简单反馈一下。",
            "#{#学生昵称}#{#家长称谓}，我整理了一下孩子这周的学习情况，和您同步一下。",
            "#{#学生昵称}#{#家长称谓}，这周的课程数据我看过了，给您说一下孩子的具体表现。",
            "#{#学生昵称}#{#家长称谓}，我结合孩子本周的课程和作答情况，给您做个反馈。",
            "#{#学生昵称}#{#家长称谓}，和您说一下孩子这一周的课程完成和学习情况。",
        ],
        "completion_finished": [
            "孩子这周两节课都已经学完了，整体学习节奏跟得上。",
            "本周两节课孩子都按时完成了，课程推进比较顺利。",
            "孩子已经完成本周两节课，整体学习进度是正常跟上的。",
            "这周两节课都有完成记录，说明孩子课后学习安排得还不错。",
            "本周课程孩子已经学完，后面主要就是把练习和知识点再梳理一遍。",
            "孩子这周的两节课都完成了，整体节奏保持得不错。",
        ],
        "evidence": [
            "孩子这周的答题情况是：{result}。",
            "具体看了一下，孩子{result}。",
            "这周孩子{result}，整体完成情况比较清楚。",
            "我看了下孩子的练习情况，{result}。",
            "从本周的数据来看，孩子{result}。",
            "孩子本周已经完成相应练习，其中{result}。",
            "再看具体的完成结果，孩子{result}。",
            "这周的练习数据里，孩子{result}。",
        ],
        "performance_high": [
            "这周整体状态很好，课堂内容吸收得也不错。",
            "这周学习状态很在线，关键内容基本都跟上了。",
            "这周完成质量很高，说明孩子上课和练习都有认真跟进。",
            "这周的表现挺亮眼，说明相关知识点已经掌握得不错。",
            "这周不管是练习还是周测都完成得很好，继续保持这个节奏。",
        ],
        "note_praise": [
            "另外，孩子这周的课程笔记也有及时上传，学习过程跟得比较认真～",
            "这周孩子还把课程笔记提交了，边学边整理的习惯很不错。",
            "笔记部分也完成了，及时把学到的内容整理下来，这一点值得继续保持。",
            "我也看到孩子上传了本周笔记，说明课后的整理有跟上。",
            "孩子这周的笔记也交上来了，整体学习节奏不错。",
            "本周笔记已经提交，愿意把知识记录下来，是一个很好的学习习惯。",
            "孩子这周有认真完成课程笔记，之后回顾和复习也会更方便。",
            "课程笔记也按要求完成了，这种及时整理的习惯挺好的。",
        ],
        "closings": [
            "刚开始学C++，先把习惯和基础打好最重要，后面有问题随时找我哈～",
            "下周我也会继续留意孩子的状态，咱们一起把基础慢慢打扎实。",
            "第一周先把课程节奏跟稳，继续保持现在的学习状态就好。",
            "这周整体先按现在的节奏稳住，后面我也会继续观察孩子的学习状态。",
            "接下来主要是把课堂里的小细节慢慢吃透，不着急，一步一步来就好。",
            "孩子目前的学习节奏我这边会持续关注，有需要调整的地方我也会及时和您同步。",
            "后面我们继续把基础知识和做题习惯一起抓起来，孩子会越来越顺的。",
            "这一阶段先让孩子保持稳定学习，遇到卡点及时处理，整体节奏就会更好。",
            "后续我也会结合孩子每周的数据继续跟进，尽量让学习反馈更具体一些。",
            "这周先这样安排，孩子能按节奏完成就很不错，后面再一点点提高要求。",
            "接下来重点还是保持连续学习，别断节奏，知识点会越学越清楚。",
            "孩子的情况我这边会持续留意，咱们先把当前课程节奏稳稳跟上。",
            "后面如果有需要重点提醒的地方，我也会单独和您说，先让孩子保持住现在的学习节奏。",
        ],
    },
    "weekly_knowledge": {
        "enabled": True,
        "weeks": {
            "1": {
                "topics": ["C++基础框架", "cout输出", "换行输出", "注释与基础书写规范"],
                "solid": "孩子对 C++ 程序的基础框架、cout 输出和换行写法理解比较清楚，代码结构也能按要求搭起来。",
                "minor": "孩子对 C++ 基础框架和输出写法整体能跟上，个别细节比如分号、引号、换行格式还需要再多留意一下。",
                "weak": "这周建议重点巩固 C++ 基础框架、cout 输出、换行格式和分号/引号这些基础细节，先把代码书写习惯稳定下来。",
            },
            "2": {
                "topics": ["算术运算符", "混合运算", "运算优先级", "表达式结果判断"],
                "solid": "孩子对算术运算符、混合运算和运算优先级掌握得比较到位，能根据表达式顺序判断结果。",
                "minor": "孩子对算术运算和混合运算整体能理解，遇到多步表达式时偶尔会在优先级或计算顺序上犹豫。",
                "weak": "这周建议重点巩固算术运算符、混合运算顺序和表达式结果判断，多练几道分步骤计算的题会更稳。",
            },
            "3": {
                "topics": ["一维数组", "数组下标从0开始", "数组输入输出", "数组遍历"],
                "solid": "孩子对一维数组的存储方式、下标从0开始以及数组输入输出掌握得比较清楚，遍历思路也比较顺。",
                "minor": "孩子对一维数组的整体概念能理解，但在下标范围、数组长度和遍历边界这些细节上还可以再巩固一下。",
                "weak": "这周建议重点巩固一维数组、下标从0开始、数组长度和遍历边界，尤其要避免下标越界和少遍历/多遍历的问题。",
            },
        },
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_workbench_config() -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, json.JSONDecodeError):
            payload = {}
    return payload


def script_config() -> dict[str, Any]:
    config = load_workbench_config()
    profile = config.get("profile") if isinstance(config.get("profile"), dict) else {}
    return deep_merge(DEFAULT_PROFILE, profile)


def data_prefix(config: dict[str, Any] | None = None) -> str:
    config = config or script_config()
    return str(config.get("data_prefix") or "demo").strip() or "demo"


def data_path(name: str, config: dict[str, Any] | None = None) -> Path:
    config = config or script_config()
    return WORKSPACE / str(config.get("files", {}).get(name) or DEFAULT_PROFILE["files"][name])


def learning_sheet_target(config: dict[str, Any] | None = None) -> dict[str, str]:
    config = config or script_config()
    dingtalk = config.get("dingtalk", {})
    return {
        "node_id": str(dingtalk.get("node_id") or DEFAULT_PROFILE["dingtalk"]["node_id"]),
        "sheet_id": str(
            dingtalk.get("learning_sheet_id")
            or DEFAULT_PROFILE["dingtalk"]["learning_sheet_id"]
        ),
        "range": str(
            dingtalk.get("learning_sheet_range")
            or DEFAULT_PROFILE["dingtalk"]["learning_sheet_range"]
        ),
        "invite_followup_sheet_name": str(
            dingtalk.get("invite_followup_sheet_name")
            or DEFAULT_PROFILE["dingtalk"]["invite_followup_sheet_name"]
        ),
        "makeup_sheet_name": str(
            dingtalk.get("makeup_sheet_name")
            or DEFAULT_PROFILE["dingtalk"]["makeup_sheet_name"]
        ),
    }


def class_mappings(config: dict[str, Any] | None = None) -> tuple[tuple[int, str], ...]:
    config = config or script_config()
    rows = config.get("classes") if isinstance(config.get("classes"), list) else []
    mappings: list[tuple[int, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            class_id = int(row.get("class_id"))
        except (TypeError, ValueError):
            continue
        label = str(row.get("label") or row.get("match_prefix") or "").strip()
        if class_id and label:
            mappings.append((class_id, label))
    return tuple(mappings)


def class_match_prefixes(config: dict[str, Any] | None = None) -> dict[int, str]:
    config = config or script_config()
    rows = config.get("classes") if isinstance(config.get("classes"), list) else []
    prefixes: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            class_id = int(row.get("class_id"))
        except (TypeError, ValueError):
            continue
        prefix = str(row.get("match_prefix") or row.get("label") or "").strip()
        if class_id and prefix:
            prefixes[class_id] = prefix
    return prefixes


def wecom_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or script_config()
    value = config.get("wecom") if isinstance(config.get("wecom"), dict) else {}
    return deep_merge(DEFAULT_PROFILE["wecom"], value)


def feedback_rules_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    root = load_workbench_config()
    if config is None:
        config = root
    value = config.get("feedback_rules") if isinstance(config.get("feedback_rules"), dict) else {}
    return deep_merge(DEFAULT_FEEDBACK_RULES, value)
