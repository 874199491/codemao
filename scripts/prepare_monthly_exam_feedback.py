"""Build a recipient-safe monthly-exam feedback manifest without external writes."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
HEADER_ALIASES = {
    "student_id": ("用户ID", "学生ID", "学员ID", "userid"),
    "student_name": ("用户姓名", "学生姓名", "学员姓名", "孩子姓名", "姓名"),
    "score": ("总得分", "总分", "成绩", "分数"),
    "participated": ("是否参加考试", "是否参加", "参考状态"),
}
BANDS = ((0, 69, "0-69"), (70, 79, "70-79"), (80, 89, "80-89"), (90, 99, "90-99"), (100, 100, "100"))
ID_KEYS = {"userid", "studentid", "student_id", "用户id", "学员id", "学生id"}


def normalize(value: object) -> str:
    return re.sub(r"[\s\u3000]+", "", str(value or "")).lower()


def cell_column(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    value = 0
    for char in (letters.group(0) if letters else ""):
        value = value * 26 + ord(char) - 64
    return value - 1


def read_xlsx_rows(path: Path) -> list[list[object]]:
    with zipfile.ZipFile(path) as book:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ET.fromstring(book.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", NS):
                shared.append("".join(node.text or "" for node in item.iterfind(".//m:t", NS)))

        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        sheet = workbook.find("m:sheets/m:sheet", NS)
        if sheet is None:
            raise RuntimeError(f"工作簿没有可读取的工作表：{path}")
        rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        target = None
        for rel in rels.findall("r:Relationship", REL_NS):
            if rel.attrib.get("Id") == rel_id:
                target = rel.attrib.get("Target")
                break
        if not target:
            raise RuntimeError(f"无法定位第一个工作表：{path}")
        sheet_path = target.lstrip("/")
        if not sheet_path.startswith("xl/"):
            sheet_path = f"xl/{sheet_path}"
        root = ET.fromstring(book.read(sheet_path))
        rows: list[list[object]] = []
        for row in root.findall(".//m:sheetData/m:row", NS):
            cells: dict[int, object] = {}
            for cell in row.findall("m:c", NS):
                index = cell_column(cell.attrib.get("r", "A1"))
                cell_type = cell.attrib.get("t")
                if cell_type == "inlineStr":
                    value: object = "".join(node.text or "" for node in cell.iterfind(".//m:t", NS))
                else:
                    value_node = cell.find("m:v", NS)
                    raw = value_node.text if value_node is not None else ""
                    if cell_type == "s" and raw != "":
                        value = shared[int(raw)]
                    elif cell_type == "b":
                        value = raw == "1"
                    else:
                        value = raw
                cells[index] = value
            if cells:
                rows.append([cells.get(index, "") for index in range(max(cells) + 1)])
        return rows


def find_header(rows: list[list[object]]) -> tuple[int, dict[str, int], list[tuple[int, int]]]:
    aliases = {key: {normalize(value) for value in values} for key, values in HEADER_ALIASES.items()}
    for row_number, row in enumerate(rows[:30]):
        headers = [str(value or "").strip() for value in row]
        normalized = [normalize(value) for value in headers]
        found: dict[str, int] = {}
        for key, candidates in aliases.items():
            for index, value in enumerate(normalized):
                if value in candidates:
                    found[key] = index
                    break
        if {"student_id", "student_name", "score"}.issubset(found):
            questions: list[tuple[int, int]] = []
            for index, header in enumerate(headers):
                match = re.search(r"第\s*(\d+)\s*题.*得分", header)
                if match:
                    questions.append((index, int(match.group(1))))
            if not questions:
                raise RuntimeError("已找到成绩表头，但没有找到‘第N题得分’列")
            return row_number, found, questions
    raise RuntimeError("无法通过表头定位学生ID、姓名和总得分列")


def numeric(value: object) -> float | None:
    text = str(value or "").strip().replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def clean_id(value: object) -> str:
    text = str(value or "").strip()
    return text[:-2] if re.fullmatch(r"\d+\.0", text) else text


def collect_roster_ids(value: object, output: set[str]) -> None:
    if isinstance(value, list):
        for item in value:
            collect_roster_ids(item, output)
    elif isinstance(value, dict):
        for key, item in value.items():
            if normalize(key) in ID_KEYS:
                candidate = clean_id(item)
                if candidate.isdigit():
                    output.add(candidate)
            collect_roster_ids(item, output)


def choose_workbook(source: Path, explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = source / path
        if not path.is_file():
            raise RuntimeError(f"指定成绩文件不存在：{path}")
        return path
    candidates = [path for path in source.glob("*.xlsx") if path.name != "已反馈.xlsx" and not path.name.startswith("~$")]
    if not candidates:
        raise RuntimeError(f"目录中没有可用的成绩明细 xlsx：{source}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def band_for(score: float) -> str | None:
    return next((name for low, high, name in BANDS if low <= score <= high), None)


def render_template(template: str, values: dict[str, object]) -> str:
    rendered = template.replace("xx", str(values["student_name"]))
    rendered = rendered.replace("ss", str(values["score"]))
    rendered = rendered.replace("tt", str(values["teacher_name"]))
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--score-file")
    parser.add_argument("--templates-dir", type=Path)
    parser.add_argument("--pdf-dir", type=Path)
    parser.add_argument("--award-dir", type=Path)
    parser.add_argument("--teacher-name")
    parser.add_argument("--roster-json", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--no-award", action="store_true")
    parser.add_argument("--no-wrong-report", action="store_true")
    args = parser.parse_args()

    source = args.source_dir.resolve()
    workbook = choose_workbook(source, args.score_file)
    templates_dir = (args.templates_dir or source / "话术").resolve()
    pdf_dir = (args.pdf_dir or source / "全班错题报告").resolve()
    award_dir = (args.award_dir or source / "已生成奖状").resolve()
    output_dir = args.output_dir.resolve()
    messages_dir = output_dir / "messages"
    messages_dir.mkdir(parents=True, exist_ok=True)

    teacher_name = (args.teacher_name or "").strip()
    teacher_file = templates_dir / "tt.txt"
    if not teacher_name and teacher_file.is_file():
        teacher_name = teacher_file.read_text(encoding="utf-8-sig").strip()
    if not teacher_name:
        raise RuntimeError("缺少教师称呼：请提供 --teacher-name 或 话术/tt.txt")

    templates: dict[str, str] = {}
    for _, _, band in BANDS:
        path = templates_dir / f"{band}.txt"
        if not path.is_file():
            raise RuntimeError(f"缺少五档话术文件：{path}")
        templates[band] = path.read_text(encoding="utf-8-sig").strip()

    roster_ids: set[str] = set()
    roster_verified = bool(args.roster_json and args.roster_json.is_file())
    if roster_verified:
        collect_roster_ids(json.loads(args.roster_json.read_text(encoding="utf-8-sig")), roster_ids)
        if not roster_ids:
            raise RuntimeError(f"学员名单文件中没有找到学生ID：{args.roster_json}")

    rows = read_xlsx_rows(workbook)
    header_row, columns, question_columns = find_header(rows)
    data_rows = rows[header_row + 1 :]
    question_max: dict[int, float] = {}
    for index, _ in question_columns:
        values = [numeric(row[index] if index < len(row) else "") for row in data_rows]
        usable = [value for value in values if value is not None]
        question_max[index] = max(usable) if usable else 0.0

    raw_students: list[dict[str, object]] = []
    for row_index, row in enumerate(data_rows, start=header_row + 2):
        student_id = clean_id(row[columns["student_id"]] if columns["student_id"] < len(row) else "")
        student_name = str(row[columns["student_name"]] if columns["student_name"] < len(row) else "").strip()
        score = numeric(row[columns["score"]] if columns["score"] < len(row) else "")
        if not student_id and not student_name and score is None:
            continue
        raw_students.append({"row": row_index, "student_id": student_id, "student_name": student_name, "score": score, "cells": row})

    id_counts = Counter(item["student_id"] for item in raw_students if item["student_id"])
    name_counts = Counter(item["student_name"] for item in raw_students if item["student_name"])
    manifest_rows: list[dict[str, object]] = []
    for item in raw_students:
        student_id = str(item["student_id"])
        student_name = str(item["student_name"])
        score = item["score"]
        blockers: list[str] = []
        if not student_id.isdigit():
            blockers.append("学生ID缺失或格式异常")
        if not student_name:
            blockers.append("学生姓名缺失")
        if score is None or not 0 <= score <= 100:
            blockers.append("成绩缺失或不在0-100")
        if id_counts.get(student_id, 0) > 1:
            blockers.append("成绩表中学生ID重复")
        if name_counts.get(student_name, 0) > 1:
            blockers.append("成绩表中学生姓名重复，附件匹配不安全")
        if roster_verified and student_id not in roster_ids:
            blockers.append("不在当前老师学员名单")
        if not roster_verified:
            blockers.append("未提供当前老师学员名单，仅可预览")

        band = band_for(float(score)) if score is not None else None
        if not band:
            blockers.append("无法确定五档话术")
        wrong_questions: list[int] = []
        cells = item["cells"]
        for index, question_number in question_columns:
            value = numeric(cells[index] if index < len(cells) else "")
            maximum = question_max.get(index, 0)
            if value is not None and maximum > 0 and value < maximum:
                wrong_questions.append(question_number)
        wrong_count = len(wrong_questions)
        question_count = len(question_columns)
        pdf = pdf_dir / f"{student_name}_错题解析.pdf"
        image = award_dir / f"{student_name}_奖状.png"
        if wrong_count > 0 and not args.no_wrong_report and not pdf.is_file():
            blockers.append("有错题但缺少同名错题解析PDF")
        if not args.no_award and not image.is_file():
            blockers.append("缺少同名奖状图片")

        display_score = "" if score is None else (str(int(score)) if float(score).is_integer() else str(score))
        values = {
            "student_name": student_name,
            "score": display_score,
            "wrong_count": wrong_count,
            "correct_count": max(0, question_count - wrong_count),
            "question_count": question_count,
            "wrong_questions": "、".join(str(value) for value in wrong_questions) or "无",
            "teacher_name": teacher_name,
        }
        message = render_template(templates[band], values) if band else ""
        message_path = messages_dir / f"{student_id}_{student_name}.txt"
        message_path.write_text(message, encoding="utf-8")
        manifest_rows.append(
            {
                "row": item["row"], "student_id": student_id, "student_name": student_name,
                "score": score, "band": band, "wrong_questions": wrong_questions,
                "wrong_count": wrong_count, "message": message, "message_file": str(message_path),
                "pdf": str(pdf) if wrong_count > 0 and not args.no_wrong_report else "", "award": "" if args.no_award else str(image),
                "roster_verified": roster_verified, "send_ready": not blockers, "blockers": blockers,
            }
        )

    payload = {
        "score_workbook": str(workbook), "teacher_name": teacher_name,
        "roster_json": str(args.roster_json.resolve()) if args.roster_json else "",
        "roster_verified": roster_verified, "student_count": len(manifest_rows),
        "ready_count": sum(bool(item["send_ready"]) for item in manifest_rows),
        "blocked_count": sum(not bool(item["send_ready"]) for item in manifest_rows),
        "students": manifest_rows,
    }
    manifest_json = output_dir / "manifest.json"
    manifest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        fields = ("student_id", "student_name", "score", "band", "wrong_count", "send_ready", "blockers")
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for item in manifest_rows:
            writer.writerow({key: ("；".join(item["blockers"]) if key == "blockers" else item[key]) for key in fields})
    print(json.dumps({key: payload[key] for key in ("score_workbook", "student_count", "ready_count", "blocked_count")}, ensure_ascii=False, indent=2))
    print(f"Manifest: {manifest_json}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise

