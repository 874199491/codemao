#!/usr/bin/env python3
"""Generate a per-student "知识点补弱 + 错题解析" PDF from CRM question data.

Reads a single student's question-detail JSON (produced by
fetch_single_student_questions.mjs) and renders a PDF that:
  1. explains each weak knowledge point (definition + common pitfalls + example), and
  2. walks through each weak knowledge point with one representative wrong question
     (stem, options, answer summary, and a real solution).

Rendering uses ReportLab's built-in STSong-Light CID font so Chinese text has
correct, natural spacing (no wide gaps).

Usage:
  python generate_weak_point_report.py --student-json <path> --name 潘晓宇 --out <pdf>
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import random
import re
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

PARSER = None


def load_local_ai_env() -> None:
    """Load optional local AI credentials from ignored config files.

    Supported format is simple KEY=VALUE lines, for example:
      DOUBAO_API_KEY=...
      DOUBAO_MODEL=...
    Existing environment variables win over file values.
    """
    root = Path(__file__).resolve().parents[1]
    for path in (root / "config" / "doubao.env", root / "config" / "doubao.env.example", root / ".env"):
        if not path.exists():
            continue
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue


load_local_ai_env()


def _build_parser():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--student-json", action="append", required=True, type=Path,
                        help="学员题目明细 JSON，可多次传入以合并多课时（如两课）")
    parser.add_argument("--course-title", default="", help="如：第13课 12-char 和 bool")
    parser.add_argument("--name", default="", help="学生姓名，默认取数据或留空")
    parser.add_argument("--out", required=True, type=Path, help="输出 PDF 路径")
    parser.add_argument("--detail-threshold", type=int, default=2,
                        help="错题数 ≥ 此值的知识点才写“知识点详解”，默认 2")
    parser.add_argument("--ai-provider", choices=["auto", "none", "doubao"], default="auto",
                        help="知识点解释和题目解析生成方式：auto=检测到豆包配置则调用，否则本地兜底；none=仅本地；doubao=强制豆包")
    parser.add_argument("--ai-model", default=os.getenv("DOUBAO_MODEL") or os.getenv("ARK_MODEL") or "",
                        help="豆包/火山方舟模型或 endpoint ID；也可用 DOUBAO_MODEL/ARK_MODEL 环境变量")
    parser.add_argument("--ai-base-url", default=os.getenv("DOUBAO_BASE_URL") or os.getenv("ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3",
                        help="OpenAI 兼容接口地址，默认火山方舟 Ark chat completions")
    parser.add_argument("--ai-cache", type=Path, default=None,
                        help="AI 生成缓存文件，默认 data/weak-report-ai-cache.json")
    parser.add_argument("--ai-timeout", type=int, default=90,
                        help="AI 接口超时时间（秒）")
    parser.add_argument("--knowledge-label", action="append", default=[],
                        help="统一知识点标签，可多次传入；用于整周统一讲解")
    parser.add_argument("--knowledge-json", type=Path, default=None,
                        help="统一知识点讲解 JSON；存在则直接读取，不存在且启用 AI 时生成")
    parser.add_argument("--skip-question-analysis", action="store_true", default=True,
                        help="只展示错题答案，不生成逐题解析，默认开启")
    return parser


# ---------------------------------------------------------------------------
# 知识点讲解映射：题目系统标签 -> 讲解正文（定义 / 易错点 / 示例）
# ---------------------------------------------------------------------------
KNOWLEDGE = {
    "字符类型": {
        "title": "字符类型 char",
        "body": (
            "char 用来保存一个字符，比如一个字母、数字或符号。在 C++ 里，字符必须用一对"
            "英文单引号括起来，例如 'A'、'Q'、'7'、'\\n'。一个 char 变量只能存放一个字符。"
        ),
        "pitfalls": [
            "字符一定用英文单引号 ' '，字符串才用双引号 \" \"。",
            "'Q' 是字符，'Q' 的类型就是 char，这是判断题里最容易踩的坑。",
            "字符 '7' 和数字 7 不是一回事，前者是字符型 char，后者是整型 int。",
        ],
        "example": "char c = 'A';   // 正确：一个英文字符\nchar d = '7';   // 正确：字符 7\nchar e = \"A\";   // 错误：双引号是字符串，不是字符",
    },
    "字符定义": {
        "title": "字符型变量的定义",
        "body": (
            "定义一个字符型变量要在类型名后面跟变量名，并用单引号赋一个字符。"
            "格式：char 变量名 = 字符;。注意右边必须是单个字符，且是英文单引号。"
        ),
        "pitfalls": [
            "char c = 'A'; 这样的定义才对。",
            "char c = \"A\"; 错误：双引号是字符串字面量。",
            "char c = '';  错误：空单引号里必须有一个字符。",
            "char c = '\\'; 错误：单个反斜杠需要转义，要写成 '\\\\'。",
        ],
        "example": "char grade = 'A';      // 正确\nchar empty = '';       // 错误：空字符\nchar q = \"A\";         // 错误：字符串",
    },
    "单引号": {
        "title": "单引号与字符字面量",
        "body": (
            "单引号 ' ' 用来表示一个字符字面量，比如 'A'、'a'、'0'、'\\n'。"
            "它的里面只能放一个字符（或一个转义字符）。记住：字符用单引号，字符串用双引号。"
        ),
        "pitfalls": [
            "一个字符 = 单引号包一个字符，如 'A'。",
            "多个字符必须用双引号，如 \"AB\"。",
            "输入一串英文单引号时，中间不能有空格。",
        ],
        "example": "'A'   是字符 char\n\"AB\"  是字符串\n''    是连续两个空单引号（注意中间无空格）",
    },
    "bool": {
        "title": "布尔类型 bool",
        "body": (
            "bool 表示真假，只有两个值：true（真，等于 1）和 false（假，等于 0）。"
            "关系表达式（如 a > b、a == b）的结果就是 bool。任何非零值转换为 bool 后都是真。"
        ),
        "pitfalls": [
            "true = 1，false = 0；输出 bool 时 true 显示 1，false 显示 0。",
            "a > b 成立返回 true，不成立返回 false；比较用 == 而不是 =。",
            "bool ok = true; 输出 ok 会得到 1。",
        ],
        "example": "bool ok = true;\ncout << ok;   // 输出 1\nbool bad = false;\ncout << bad;  // 输出 0",
    },
    "转义字符": {
        "title": "转义字符",
        "body": (
            "转义字符用反斜杠 \\ 加一个字符，表示一些特殊字符或控制字符。"
            "比如 '\\n' 表示换行，'\\t' 表示制表符，'\\\\' 表示一个反斜杠，'\\'' 表示单引号。"
        ),
        "pitfalls": [
            "'\\n' 是一个字符（换行符），类型仍是 char。",
            "想表示一个反斜杠本身，要写 '\\\\'，因为 \\ 会被转义。",
            "转义字符要用单引号包起来（作为字符时）。",
        ],
        "example": "char n = '\\n';      // 换行符，是一个字符\ncout << \"a\\nb\";  // 输出 a 换行 b",
    },
    "代码规范": {
        "title": "代码书写规范",
        "body": (
            "好的 C++ 代码要有清晰的风格：语句以分号结尾、变量先定义再使用、"
            "输出用 cout <<、输入用 cin >>，方向不能反；程序要有 main 函数作为入口。"
        ),
        "pitfalls": [
            "cin 用 >> 输入，cout 用 << 输出，箭头方向别写反。",
            "每条语句末尾要加分号 ;。",
            "变量使用前必须先定义，并注意类型与值的匹配。",
        ],
        "example": "int a;\ncin >> a;      // 输入\ncout << a << endl;  // 输出，别忘了分号",
    },
    "数据类型区分": {
        "title": "数据类型区分",
        "body": (
            "C++ 常见类型：int 整数、double 小数、char 字符、bool 真假。"
            "不同变量要放进合适的类型。cin 读入时按变量类型解析，char 只读一个字符，"
            "int 会读一个整数。混用类型容易出问题。"
        ),
        "pitfalls": [
            "cin >> c 遇到的是字符时，只取一个字符。",
            "cin >> 整数时，会把紧随其后的空格/回车留在缓冲区。",
            "字符串里「X 2333」若用 char 逐一读，注意空格。",
        ],
        "example": "char c;  cin >> c;   // 读一个字符\nint x;    cin >> x;   // 读一个整数",
    },
}

DEFAULT_KNOWLEDGE = {
    "title": "知识点小结",
    "body": "本知识点请结合课堂讲解与错题复习，重点理解概念与易错点。",
    "pitfalls": ["注意区分易混概念，做完错题后回头订正。"],
    "example": "对照错题，把相关知识点再过一遍。",
}



# ---------------------------------------------------------------------------
# Optional AI enrichment (Doubao / Volcengine Ark OpenAI-compatible API)
# ---------------------------------------------------------------------------
def _compact_text(value: str, limit: int = 1600) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _cache_key(kind: str, payload: dict) -> str:
    raw = json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AIHelper:
    def __init__(self, args):
        self.provider = args.ai_provider
        self.model = str(args.ai_model or "").strip()
        self.base_url = str(args.ai_base_url or "").rstrip("/")
        self.timeout = int(args.ai_timeout or 45)
        self.cache_path = args.ai_cache or (Path(__file__).resolve().parents[1] / "data" / "weak-report-ai-cache.json")
        self.cache: dict[str, str] = {}
        self.enabled = False
        self.api_key = os.getenv("DOUBAO_API_KEY") or os.getenv("ARK_API_KEY") or os.getenv("VOLCENGINE_API_KEY") or ""
        if self.cache_path.is_file():
            try:
                loaded = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.cache = {str(k): str(v) for k, v in loaded.items()}
            except Exception:
                self.cache = {}
        if self.provider == "none":
            return
        if self.provider == "doubao" and (not self.api_key or not self.model):
            raise RuntimeError("已指定 --ai-provider doubao，但缺少 DOUBAO_API_KEY/ARK_API_KEY 或 DOUBAO_MODEL/ARK_MODEL")
        self.enabled = bool(self.api_key and self.model and self.provider in {"auto", "doubao"})

    def save(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")

    def chat(self, kind: str, payload: dict, prompt: str, max_tokens: int = 900) -> str:
        if not self.enabled:
            return ""
        key = _cache_key(kind, {"model": self.model, **payload})
        if key in self.cache:
            return self.cache[key]
        url = self.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是少儿C++编程老师，给五六年级学生写错题报告。语言要具体、短句、像老师讲题，不要空泛鼓励，不要编造题目不存在的信息。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.25,
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = str(data["choices"][0]["message"]["content"]).strip()
            content = re.sub(r"\n{3,}", "\n\n", content)
            if content:
                self.cache[key] = content
                self.save()
            return content
        except Exception as exc:
            print(f"AI 生成失败，已使用本地兜底：{exc}", file=sys.stderr)
            return ""


def question_payload(q: dict) -> dict:
    options = []
    for o in q.get("options") or []:
        seq = int(o.get("seq") or 0)
        options.append({
            "letter": option_letter(seq),
            "text": strip_html(o.get("text")),
            "is_correct": bool(o.get("isCorrect")),
            "is_chosen": bool(o.get("isChosen")),
        })
    return {
        "type": q.get("type"),
        "stem": strip_html(q.get("description")),
        "options": options,
        "user_answer": q.get("userAnswer"),
        "normal_answer": q.get("normalAnswer"),
        "knowledge": q.get("knowledgeArr") or [],
    }


def ai_knowledge_text(ai: AIHelper, label: str, questions: list[dict], count: int) -> dict | None:
    samples = [question_payload(q) for q in questions[:3]]
    prompt = f"""
请为学生错题报告生成一个知识点讲解，知识点是：{label}，本周该知识点错了 {count} 题。

参考错题：
{json.dumps(samples, ensure_ascii=False, indent=2)}

请只返回 JSON，不要加 Markdown 代码块，格式如下：
{{
  "title": "适合放在报告里的知识点标题",
  "body": "用五六年级学生能懂的话解释这个知识点，结合题目考法，120字以内",
  "pitfalls": ["易错点1", "易错点2", "易错点3"],
  "example": "一段很短的C++例子或做题口诀，允许换行"
}}
""".strip()
    text = ai.chat("knowledge", {"label": label, "questions": samples, "count": count}, prompt, max_tokens=800)
    if not text:
        return None
    try:
        parsed = json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.I | re.M).strip())
    except json.JSONDecodeError:
        return {"title": label, "body": text, "pitfalls": [], "example": ""}
    if not isinstance(parsed, dict):
        return None
    return {
        "title": str(parsed.get("title") or label).strip(),
        "body": str(parsed.get("body") or "").strip(),
        "pitfalls": [str(x).strip() for x in (parsed.get("pitfalls") or []) if str(x).strip()][:4],
        "example": str(parsed.get("example") or "").strip(),
    }


def ai_solution_text(ai: AIHelper, q: dict, knowledge_label: str) -> str:
    payload = question_payload(q)
    prompt = f"""
请给学生错题报告写一道题的解析。

知识点：{knowledge_label}
题目信息：
{json.dumps(payload, ensure_ascii=False, indent=2)}

要求：
1. 先说明这题考什么。
2. 指出学生错因，不能空泛。
3. 给出正确解法或判断步骤。
4. 最后给一个提醒口诀。
5. 180字以内，适合五六年级学生和家长看。
""".strip()
    return ai.chat("solution", {"knowledge": knowledge_label, "question": payload}, prompt, max_tokens=700)



def ai_solution_bundle(ai: AIHelper, questions: list[dict]) -> dict[str, str]:
    if not ai.enabled or not questions:
        return {}
    rows = []
    for q in questions:
        labs = classify(q)[1] or ["未知"]
        rows.append({
            "id": question_key(q),
            "knowledge": str(labs[0]),
            "question": question_payload(q),
        })
    prompt = f"""
请给学生错题报告中的每一道题生成具体解析。

题目列表：
{json.dumps(rows, ensure_ascii=False, indent=2)}

请只返回 JSON，不要 Markdown 代码块，格式如下：
{{
  "solutions": {{
    "题目id": "解析内容"
  }}
}}
要求：
1. 每一个题目 id 都必须返回解析，不能漏题。
2. 解析要结合题干、学生选择、正确选项说明为什么错、正确怎么判断。
3. 不要写“请对照正确选项复习”这种空话。
4. 每题 80-140 字，适合五六年级学生和家长看。
5. 如果题干不完整，也要根据选项和知识点写出可用的判断思路。
""".strip()
    text = ai.chat("display_question_solutions_v1", {"questions": rows}, prompt, max_tokens=2200)
    if not text:
        return {}
    try:
        parsed = json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.I | re.M).strip())
    except json.JSONDecodeError:
        return {}
    solutions = parsed.get("solutions") if isinstance(parsed, dict) else None
    if not isinstance(solutions, dict):
        return {}
    return {str(k): str(v).strip() for k, v in solutions.items() if str(v).strip()}


def question_key(q: dict) -> str:
    payload = question_payload(q)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def ai_report_bundle(ai: AIHelper, lab_counter: Counter, by_label: dict, representative: dict) -> dict:
    """Generate all AI text for one student's report in a single API call."""
    if not ai.enabled:
        return {"knowledge": {}, "solutions": {}}
    labels = []
    for lab, cnt in lab_counter.most_common():
        labels.append({
            "label": str(lab),
            "wrong_count": int(cnt),
            "sample_questions": [question_payload(q) for q in (by_label.get(lab) or [])[:2]],
        })
    reps = []
    for lab, q in representative.items():
        if not q:
            continue
        reps.append({
            "id": question_key(q),
            "knowledge": str(lab),
            "question": question_payload(q),
        })
    prompt = f"""
请为一个学生的 C++ 错题报告生成更细致的知识点解释和错题解析。

知识点列表：
{json.dumps(labels, ensure_ascii=False, indent=2)}

需要解析的代表错题：
{json.dumps(reps, ensure_ascii=False, indent=2)}

请只返回 JSON，不要 Markdown 代码块，格式如下：
{{
  "knowledge": {{
    "知识点标签": {{
      "title": "报告标题",
      "body": "120字以内，讲清概念和这类题怎么考",
      "pitfalls": ["易错点1", "易错点2", "易错点3"],
      "example": "短例子或做题口诀"
    }}
  }},
  "solutions": {{
    "题目id": "180字以内。说明考什么、学生错因、正确步骤、提醒口诀。"
  }}
}}
语言要像少儿编程老师讲给五六年级学生和家长听，具体、短句，不要空泛鼓励，不要编造题目没有的信息。
""".strip()
    text = ai.chat("report_bundle", {"labels": labels, "representative": reps}, prompt, max_tokens=2600)
    if not text:
        return {"knowledge": {}, "solutions": {}}
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.I | re.M).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return {"knowledge": {}, "solutions": {}}
    if not isinstance(parsed, dict):
        return {"knowledge": {}, "solutions": {}}
    knowledge = parsed.get("knowledge") if isinstance(parsed.get("knowledge"), dict) else {}
    solutions = parsed.get("solutions") if isinstance(parsed.get("solutions"), dict) else {}
    return {"knowledge": knowledge, "solutions": solutions}

def fallback_knowledge(label: str) -> dict:
    if label in KNOWLEDGE:
        return KNOWLEDGE[label]
    return {"title": str(label or "知识点"), "body": "", "pitfalls": [], "example": ""}


def normalize_knowledge_item(label: str, value) -> dict:
    if not isinstance(value, dict):
        return fallback_knowledge(label)
    return {
        "title": str(value.get("title") or label).strip(),
        "body": str(value.get("body") or "").strip(),
        "pitfalls": [str(x).strip() for x in (value.get("pitfalls") or []) if str(x).strip()][:4],
        "example": str(value.get("example") or "").strip(),
    }


def is_useful_knowledge(k: dict) -> bool:
    body = str((k or {}).get("body") or "").strip()
    example = str((k or {}).get("example") or "").strip()
    pitfalls = [str(x).strip() for x in ((k or {}).get("pitfalls") or []) if str(x).strip()]
    generic_markers = [
        "这部分和“",
        "建议结合课堂回放、笔记和错题再过一遍",
        "注意区分易混概念，做完错题后回头订正",
        "对照错题，把相关知识点再过一遍",
    ]
    merged = body + "\n" + example + "\n" + "\n".join(pitfalls)
    if any(marker in merged for marker in generic_markers):
        return False
    return bool(body or example or pitfalls)


def load_or_generate_shared_knowledge(ai: AIHelper, labels: list[str], course_title: str, path: Path | None) -> dict:
    clean_labels = []
    for label in labels:
        label = str(label or "").strip()
        if label and label not in clean_labels:
            clean_labels.append(label)
    if path and path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("knowledge"), dict):
                return {str(k): normalize_knowledge_item(str(k), v) for k, v in data["knowledge"].items()}
        except Exception:
            pass
    fallback = {label: fallback_knowledge(label) for label in clean_labels}
    if not clean_labels or not ai.enabled:
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"course_title": course_title, "labels": clean_labels, "knowledge": fallback}, ensure_ascii=False, indent=2), encoding="utf-8")
        return fallback
    prompt = f"""
请为第{course_title or '本周'} C++ 错题报告生成统一知识点讲解。所有学员本周使用同一份讲解，不要针对单个学生。

知识点标签：
{json.dumps(clean_labels, ensure_ascii=False, indent=2)}

请只返回 JSON，不要 Markdown 代码块，格式如下：
{{
  "knowledge": {{
    "知识点标签": {{
      "title": "报告标题",
      "body": "120字以内，讲清概念、典型考法和学习重点",
      "pitfalls": ["易错点1", "易错点2", "易错点3"],
      "example": "短例子或做题口诀"
    }}
  }}
}}
强制要求：
1. knowledge 里面必须为上面的每一个知识点标签分别返回一个同名键。
2. 键名必须完全等于原知识点标签，不能改名，不能合并多个标签，不能新增总标签。
3. 每个标签都要有独立讲解，body 不能为空。
语言要像少儿 C++ 老师讲给五六年级学生和家长听，具体、短句，不要空泛鼓励。
""".strip()
    text = ai.chat("shared_week_knowledge_v2", {"course_title": course_title, "labels": clean_labels}, prompt, max_tokens=3200)
    if text:
        try:
            parsed = json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.I | re.M).strip())
            knowledge = parsed.get("knowledge") if isinstance(parsed, dict) else None
            if isinstance(knowledge, dict):
                fallback.update({str(k): normalize_knowledge_item(str(k), v) for k, v in knowledge.items()})
        except Exception:
            pass
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"course_title": course_title, "labels": clean_labels, "knowledge": fallback}, ensure_ascii=False, indent=2), encoding="utf-8")
    return fallback

# ---------------------------------------------------------------------------
def strip_html(text: str) -> str:
    text = html.unescape(str(text or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def option_letter(i) -> str:
    return chr(ord("A") + i)


def classify(q):
    ua = q.get("userAnswer")
    na = q.get("normalAnswer")
    chosen = [str(o.get("seq")) for o in (q.get("options") or []) if o.get("isChosen")]
    correct = [str(o.get("seq")) for o in (q.get("options") or []) if o.get("isCorrect")]
    has_ans = bool(ua is not None and str(ua).strip()) or bool(chosen)
    if not has_ans:
        return "unanswered", None
    if chosen and correct:
        ok = sorted(chosen) == sorted(correct)
    else:
        ok = bool(ua and na and str(ua).strip() == str(na).strip())
    if ok:
        return "correct", None
    return "wrong", (q.get("knowledgeArr") or [])


KNOWLEDGE_LABEL_HINT = {
    "字符类型": "注意：英文字符要用单引号包起来，'Q'、'A' 这些都是 char 类型。",
    "字符定义": "定义一个字符变量，右边必须是 单引号+一个字符；双引号是字符串，不能用。",
    "单引号": "牢记口诀：字符用单引号 ' '，字符串用双引号 \" \"；空单引号中间不能有字符。",
    "bool": "bool 只有 true/false，输出时 true=1、false=0；关系表达式的结果就是 bool。",
    "转义字符": "转义字符以一个反斜杠表示特殊字符，'\\n' 是换行，是一个字符。",
    "代码规范": "注意 cin>>、cout<< 的方向，语句结尾要有分号，变量先定义后使用。",
    "数据类型区分": "不同类型要放进合适变量；char 只存一个字符，int 存整数。",
}


def build_solution(q, knowledge_label):
    lab = knowledge_label or "本知识点"
    lines = []
    hint = KNOWLEDGE_LABEL_HINT.get(knowledge_label, "")
    if hint:
        lines.append(hint)
    else:
        lines.append(f"这道题考查“{lab}”的理解，请对照正确选项复习该知识点。")
    return "".join(line for line in lines if line)


def load_student(paths):
    if isinstance(paths, (list, tuple)):
        file_paths = paths
    else:
        file_paths = [paths]
    items = []
    for path in file_paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        qd = data.get("data") or {}
        for _ckey, steps in qd.items():
            for step in steps or []:
                for q in step.get("newCourseData") or []:
                    items.append(q)
    return items


# ---------------------------------------------------------------------------
# ReportLab rendering
# ---------------------------------------------------------------------------
GREEN = (0.21, 0.48, 0.29)
ORANGE = (0.59, 0.35, 0.0)
GRAY = (0.35, 0.35, 0.35)
LIGHT_GRAY = (0.6, 0.6, 0.6)
DARK = (0.12, 0.12, 0.12)
RED = (0.78, 0.27, 0.27)
WHITE = (1, 1, 1)


def main():
    global PARSER
    parser = _build_parser()
    args = parser.parse_args()

    ai = AIHelper(args)
    items = load_student(args.student_json)
    wrong = [q for q in items if classify(q)[0] == "wrong"]
    wrong.sort(key=lambda q: str(q.get("name") or ""))
    # 报告里不展示填空题；知识点讲解也只围绕实际展示的错题。
    report_wrong = [q for q in wrong if int(q.get("type") or 0) != 3]

    if not wrong:
        print(f"该学员（{args.name or args.student_json}）无真实错题，不生成报告。", file=sys.stderr)
        return 2
    if not report_wrong:
        print(f"该学员（{args.name or args.student_json}）错题均为填空题，不生成报告。", file=sys.stderr)
        return 2

    lab_counter = Counter()
    by_label = defaultdict(list)
    for q in report_wrong:
        labs = classify(q)[1] or ["未知"]
        for lab in labs:
            lab_counter[lab] += 1
        by_label[labs[0]].append(q)
    representative = {}
    for lab in lab_counter:
        representative[lab] = by_label[lab][0] if by_label[lab] else None

    student_labels = [str(lab) for lab, _ in lab_counter.most_common()]
    requested_labels = args.knowledge_label or student_labels
    shared_knowledge = load_or_generate_shared_knowledge(ai, requested_labels, args.course_title, args.knowledge_json)

    if not lab_counter:
        print(f"该学员（{args.name or args.student_json}）无可识别错题知识点，不生成报告。", file=sys.stderr)
        return 2

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import (
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether,
    )

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    FONT = "STSong-Light"

    doc = SimpleDocTemplate(
        str(args.out), pagesize=A4,
        leftMargin=2.0 * cm, rightMargin=2.0 * cm,
        topMargin=1.7 * cm, bottomMargin=1.6 * cm,
        title="知识点与错题解析",
    )
    content = []

    # styles
    st_title = ParagraphStyle("title", fontName=FONT, fontSize=22, leading=28,
                              alignment=1, textColor=DARK, spaceAfter=2)
    st_sub = ParagraphStyle("sub", fontName=FONT, fontSize=12.5, leading=18,
                            alignment=1, textColor=GRAY, spaceAfter=1)
    st_bar = ParagraphStyle("bar", fontName=FONT, fontSize=14.5, leading=16,
                            textColor=WHITE, spaceBefore=8, spaceAfter=6)
    st_sec = ParagraphStyle("sec", fontName=FONT, fontSize=14.5, leading=20,
                            textColor=GREEN, spaceBefore=8, spaceAfter=3)
    st_body = ParagraphStyle("body", fontName=FONT, fontSize=12, leading=17,
                             textColor=DARK, spaceAfter=2)
    st_pitfall = ParagraphStyle("pitfall", fontName=FONT, fontSize=11, leading=15.5,
                                textColor=ORANGE, spaceAfter=2)
    st_example = ParagraphStyle("example", fontName=FONT, fontSize=10.5, leading=14.5,
                                textColor=GRAY, spaceAfter=4)
    st_qhead = ParagraphStyle("qhead", fontName=FONT, fontSize=13.5, leading=18,
                              textColor=GREEN, spaceBefore=6, spaceAfter=2)
    st_stem = ParagraphStyle("stem", fontName=FONT, fontSize=11.5, leading=16.5,
                             textColor=DARK, spaceAfter=1)
    st_opt_good = ParagraphStyle("optgood", fontName=FONT, fontSize=11.5, leading=16,
                                 textColor=GREEN, leftIndent=12, spaceAfter=1)
    st_opt_bad = ParagraphStyle("optbad", fontName=FONT, fontSize=11.5, leading=16,
                                textColor=RED, leftIndent=12, spaceAfter=1)
    st_opt_norm = ParagraphStyle("optnorm", fontName=FONT, fontSize=11.5, leading=16,
                                 textColor=(0.35, 0.35, 0.35), leftIndent=12, spaceAfter=1)
    st_ans = ParagraphStyle("ans", fontName=FONT, fontSize=10.5, leading=14.5,
                            textColor=GRAY, leftIndent=12, spaceAfter=1)
    st_sol = ParagraphStyle("sol", fontName=FONT, fontSize=11.5, leading=16.5,
                            textColor=DARK, spaceBefore=2, spaceAfter=3)
    st_footer = ParagraphStyle("footer", fontName=FONT, fontSize=9, leading=12,
                               alignment=1, textColor=LIGHT_GRAY, spaceBefore=10)

    def esc(text):
        return html.escape(str(text or ""))

    def bar(title):
        t = Table([[Paragraph(esc(title), st_bar)]], colWidths=[doc.width])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), GREEN),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        return t

    name = args.name or "该学员"
    # 标题
    content.append(Paragraph("知识点与错题解析", st_title))
    content.append(Paragraph(f"学员：{esc(name)}", st_sub))
    if args.course_title:
        content.append(Paragraph(f"课程：{esc(args.course_title)}", st_sub))
    content.append(Spacer(1, 6))

    # 知识点讲解：单个学员只展示自己实际错过的知识点，避免出现“错 0 题”。
    detail_labels = [lab for lab, _cnt in lab_counter.most_common()]
    solved_section_no = "一"
    if detail_labels:
        content.append(bar("一、知识点讲解"))
        content.append(Spacer(1, 4))
        for lab in detail_labels:
            cnt = lab_counter[lab]
            k = shared_knowledge.get(str(lab)) or fallback_knowledge(str(lab))
            if not is_useful_knowledge(k):
                continue
            block = [
                Paragraph(f"◇ {esc(k['title'])}　<font color='#8a8a8a'>（{esc(lab)}）</font>", st_sec),
                Paragraph(esc(k["body"]), st_body),
            ]
            if k.get("pitfalls"):
                block.append(Paragraph("易错：" + esc("；".join(k["pitfalls"])), st_pitfall))
            if k.get("example"):
                block.append(Paragraph("示例：", st_pitfall))
                for line in k["example"].split("\n"):
                    block.append(Paragraph(esc(line), st_example))
            content.append(KeepTogether(block))
            content.append(Spacer(1, 4))
        solved_section_no = "二"

    # （二）错题整理：展示全部非填空错题，不再限制为每个知识点一道代表题。
    content.append(Spacer(1, 6))
    content.append(bar(solved_section_no + "、错题整理"))
    content.append(Spacer(1, 2))
    type_map = {1: "单选题", 2: "多选题", 3: "填空题", 0: "未知"}
    ordered_wrong = []
    for lab, _ in lab_counter.most_common():
        ordered_wrong.extend(by_label.get(lab) or [])
    unique_wrong = []
    seen_questions = set()
    for q in ordered_wrong:
        qid = question_key(q)
        if qid in seen_questions:
            continue
        seen_questions.add(qid)
        unique_wrong.append(q)
    if len(unique_wrong) > 6:
        seed_raw = json.dumps([question_key(q) for q in unique_wrong], ensure_ascii=False, sort_keys=True)
        rng = random.Random(hashlib.sha1(seed_raw.encode("utf-8")).hexdigest())
        sample_size = min(len(unique_wrong), rng.randint(6, 8))
        display_wrong = rng.sample(unique_wrong, sample_size)
        display_wrong.sort(key=lambda q: unique_wrong.index(q))
    else:
        display_wrong = unique_wrong
    ai_solutions = ai_solution_bundle(ai, display_wrong)
    for q in display_wrong:
        labs = classify(q)[1] or ["未知"]
        knowledge_label = labs[0]
        tname = type_map.get(q.get("type"), "题")
        stem = strip_html(q.get("description"))
        ua = q.get("userAnswer")
        na = q.get("normalAnswer")
        block = [Paragraph(f"◇ {esc(knowledge_label)}　【{tname}】", st_qhead)]
        block.append(Paragraph("题干：" + esc(stem), st_stem))
        for o in q.get("options") or []:
            seq = int(o.get("seq") or 0)
            text = strip_html(o.get("text"))
            mark = "　（正确）" if o.get("isCorrect") else ""
            chosen = "　【你选了】" if o.get("isChosen") else ""
            line = f"{option_letter(seq)}. {esc(text)}{esc(mark)}{esc(chosen)}"
            if o.get("isCorrect"):
                block.append(Paragraph(line, st_opt_good))
            elif o.get("isChosen"):
                block.append(Paragraph(line, st_opt_bad))
            else:
                block.append(Paragraph(line, st_opt_norm))
        solution = ai_solutions.get(question_key(q)) or build_solution(q, knowledge_label)
        block.append(Paragraph("解析：" + esc(solution), st_sol))
        content.append(KeepTogether(block))
        content.append(Spacer(1, 5))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    doc.build(content)
    print("written:", args.out)
    print("wrong:", len(wrong))
    print("non_fill_wrong:", len(unique_wrong))
    print("displayed_non_fill_wrong:", len(display_wrong))
    print("representative:", len(representative))
    print("labs:", dict(lab_counter))


if __name__ == "__main__":
    raise SystemExit(main())



