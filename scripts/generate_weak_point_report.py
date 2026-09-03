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

import html
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

PARSER = None


def _build_parser():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--student-json", action="append", required=True, type=Path,
                        help="学员题目明细 JSON，可多次传入以合并多课时（如两课）")
    parser.add_argument("--course-title", default="", help="如：第13课 12-char 和 bool")
    parser.add_argument("--name", default="", help="学生姓名，默认取数据或留空")
    parser.add_argument("--detail-threshold", type=int, default=2,
                        help="错题数 ≥ 此值的知识点才写“知识点详解”，默认 2")
    parser.add_argument("--out", required=True, type=Path)
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

    items = load_student(args.student_json)
    wrong = [q for q in items if classify(q)[0] == "wrong"]
    wrong.sort(key=lambda q: str(q.get("name") or ""))

    if not wrong:
        print(f"该学员（{args.name or args.student_json}）无真实错题，不生成报告。", file=sys.stderr)
        return 2

    lab_counter = Counter()
    by_label = defaultdict(list)
    for q in wrong:
        labs = classify(q)[1] or ["未知"]
        for lab in labs:
            # 只保留有专属讲解映射的知识点；没有讲解的（走默认兜底）不写入报告。
            if lab not in KNOWLEDGE:
                continue
            lab_counter[lab] += 1
        if labs[0] in KNOWLEDGE:
            by_label[labs[0]].append(q)
    representative = {}
    for lab in lab_counter:
        representative[lab] = by_label[lab][0] if by_label[lab] else None

    if not lab_counter:
        print(f"该学员（{args.name or args.student_json}）的错题均无对应知识点讲解，不生成报告。", file=sys.stderr)
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
        title="知识点补弱与错题解析",
    )
    content = []

    # styles
    st_title = ParagraphStyle("title", fontName=FONT, fontSize=20, leading=26,
                              alignment=1, textColor=DARK, spaceAfter=2)
    st_sub = ParagraphStyle("sub", fontName=FONT, fontSize=11, leading=16,
                            alignment=1, textColor=GRAY, spaceAfter=1)
    st_bar = ParagraphStyle("bar", fontName=FONT, fontSize=13, leading=14,
                            textColor=WHITE, spaceBefore=8, spaceAfter=6)
    st_sec = ParagraphStyle("sec", fontName=FONT, fontSize=13, leading=18,
                            textColor=GREEN, spaceBefore=8, spaceAfter=3)
    st_body = ParagraphStyle("body", fontName=FONT, fontSize=10.5, leading=15.5,
                             textColor=DARK, spaceAfter=2)
    st_pitfall = ParagraphStyle("pitfall", fontName=FONT, fontSize=9.5, leading=14,
                                textColor=ORANGE, spaceAfter=2)
    st_example = ParagraphStyle("example", fontName=FONT, fontSize=9, leading=13,
                                textColor=GRAY, spaceAfter=4)
    st_qhead = ParagraphStyle("qhead", fontName=FONT, fontSize=12, leading=16,
                              textColor=GREEN, spaceBefore=6, spaceAfter=2)
    st_stem = ParagraphStyle("stem", fontName=FONT, fontSize=10, leading=15,
                             textColor=DARK, spaceAfter=1)
    st_opt_good = ParagraphStyle("optgood", fontName=FONT, fontSize=10, leading=14,
                                 textColor=GREEN, leftIndent=12, spaceAfter=1)
    st_opt_bad = ParagraphStyle("optbad", fontName=FONT, fontSize=10, leading=14,
                                textColor=RED, leftIndent=12, spaceAfter=1)
    st_opt_norm = ParagraphStyle("optnorm", fontName=FONT, fontSize=10, leading=14,
                                 textColor=(0.35, 0.35, 0.35), leftIndent=12, spaceAfter=1)
    st_ans = ParagraphStyle("ans", fontName=FONT, fontSize=9, leading=13,
                            textColor=GRAY, leftIndent=12, spaceAfter=1)
    st_sol = ParagraphStyle("sol", fontName=FONT, fontSize=10, leading=15,
                            textColor=DARK, spaceBefore=2, spaceAfter=3)
    st_footer = ParagraphStyle("footer", fontName=FONT, fontSize=8, leading=11,
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
    content.append(Paragraph("知识点补弱与错题解析", st_title))
    content.append(Paragraph(f"学员：{esc(name)}", st_sub))
    if args.course_title:
        content.append(Paragraph(f"课程：{esc(args.course_title)}", st_sub))
    content.append(Spacer(1, 6))

    # 知识点讲解：只要有错题的知识点都写详解；无讲解映射的除外。
    detail_labels = [lab for lab, cnt in lab_counter.most_common() if lab in KNOWLEDGE]
    solved_section_no = "一"
    if detail_labels:
        content.append(bar("一、知识点讲解"))
        content.append(Spacer(1, 4))
        for lab in detail_labels:
            cnt = lab_counter[lab]
            k = KNOWLEDGE.get(lab, DEFAULT_KNOWLEDGE)
            block = [
                Paragraph(f"◇ {esc(k['title'])}　<font color='#8a8a8a'>（{esc(lab)}，错 {cnt} 题）</font>", st_sec),
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

    # （二）错题解析（每知识点一道代表题）
    content.append(Spacer(1, 6))
    content.append(bar(solved_section_no + "、错题解析"))
    content.append(Spacer(1, 2))
    content.append(Paragraph("以下每道题标注了正确答案与解析。", st_sub))
    content.append(Spacer(1, 2))
    type_map = {1: "单选题", 2: "多选题", 3: "填空题", 0: "未知"}
    for lab, _ in lab_counter.most_common():
        q = representative[lab]
        if not q:
            continue
        knowledge_label = lab
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
        sol = build_solution(q, knowledge_label)
        block.append(Paragraph("解析：" + esc(sol), st_sol))
        content.append(KeepTogether(block))
        content.append(Spacer(1, 5))

    content.append(Paragraph("由 教师工作台·教学质检 生成", st_footer))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    doc.build(content)
    print("written:", args.out)
    print("wrong:", len(wrong))
    print("representative:", len(representative))
    print("labs:", dict(lab_counter))


if __name__ == "__main__":
    raise SystemExit(main())
