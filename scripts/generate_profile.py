#!/usr/bin/env python3
"""Generate or merge a CodeMao teacher-workbench profile."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


NODE_PATTERN = re.compile(r"/nodes/([^/?#]+)")
DEFAULT_WORKSPACE = Path(__file__).resolve().parents[1]
LEARNING_SHEET_NAME_CANDIDATES = (
    "\u5b66\u60c5\u8868",
    "\u5b66\u5458\u8868",
    "0724\u5b66\u60c5\u8868",
    "\u5b66\u751f\u8868",
)
LEARNING_HEADER_CANDIDATES = (
    "\u5b66\u751fID",
    "\u7528\u6237id",
    "\u7528\u6237ID",
    "\u5b66\u5458ID",
    "\u5b66\u751f\u59d3\u540d",
    "\u5b66\u5458\u59d3\u540d",
    "\u4e0a\u8bfe\u65f6\u95f4",
    "\u73ed\u7ea7",
)
LEARNING_COLUMN_ALIASES = {
    "student_id": (
        "\u5b66\u751fID",
        "\u7528\u6237ID",
        "\u7528\u6237id",
        "\u5b66\u5458ID",
        "\u7528\u6237\u7f16\u53f7",
        "user_id",
        "userId",
    ),
    "student_name": (
        "\u5b66\u751f\u59d3\u540d",
        "\u5b66\u5458\u59d3\u540d",
        "\u5b66\u751f\u540d\u5b57",
        "\u5b69\u5b50\u59d3\u540d",
        "\u5b69\u5b50\u540d\u5b57",
        "\u59d3\u540d",
    ),
    "class_time": (
        "\u4e0a\u8bfe\u65f6\u95f4",
        "\u73ed\u7ea7\u65f6\u95f4",
        "\u4e0a\u8bfe\u65f6\u6bb5",
        "\u73ed\u6b21",
    ),
    "class_name": (
        "\u73ed\u7ea7",
        "\u73ed\u7ea7\u540d\u79f0",
        "\u73ed\u53f7",
    ),
    "leave": (
        "\u662f\u5426\u8bf7\u5047",
        "\u8bf7\u5047",
    ),
    "leave_reason": (
        "\u8bf7\u5047\u539f\u56e0",
        "\u672a\u5230\u8bfe\u539f\u56e0",
        "\u672a\u5b8c\u8bfe\u539f\u56e0",
    ),
    "phone_followup": (
        "\u662f\u5426\u7535\u8bdd\u8ddf\u8fdb",
        "\u7535\u8bdd\u8ddf\u8fdb",
    ),
    "focus": (
        "\u91cd\u70b9\u5173\u6ce8",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-prefix", required=True)
    parser.add_argument("--node-id", default="")
    parser.add_argument("--dingtalk-url", default="")
    parser.add_argument("--learning-sheet-id", default="")
    parser.add_argument("--learning-sheet-name", default="")
    parser.add_argument("--auto-learning-sheet", action="store_true")
    parser.add_argument("--list-sheets", action="store_true")
    parser.add_argument("--validate-learning-sheet", action="store_true")
    parser.add_argument("--learning-sheet-range", default="A1:AZ300")
    parser.add_argument("--class-pool-id", type=int, default=0)
    parser.add_argument("--invite-followup-sheet-name", default="邀约跟进")
    parser.add_argument("--makeup-sheet-name", default="补课表")
    parser.add_argument(
        "--class",
        dest="classes",
        action="append",
        default=[],
        help="CRM class mapping as class_id:label:match_prefix, e.g. 123456:周五晚:周五",
    )
    parser.add_argument(
        "--class-file",
        type=Path,
        help="CSV/JSON CRM class list. CSV supports class_id/classId, class_name/className, term_name/termName.",
    )
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--completion-classes-csv", default="")
    parser.add_argument("--students-json", default="")
    parser.add_argument("--roster-csv", default="")
    parser.add_argument("--refunded-json", default="")
    parser.add_argument("--confirmed-refunded-json", default="")
    parser.add_argument("--update-config", type=Path)
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def node_id_from_url(url: str) -> str:
    match = NODE_PATTERN.search(url)
    return match.group(1) if match else ""


def parse_class(value: str) -> dict[str, Any]:
    parts = value.split(":", 2)
    if len(parts) != 3:
        raise ValueError(f"Invalid --class {value!r}; expected class_id:label:match_prefix")
    class_id_text, label, match_prefix = [part.strip() for part in parts]
    try:
        class_id = int(class_id_text)
    except ValueError as error:
        raise ValueError(f"Invalid class_id in --class {value!r}") from error
    if not label or not match_prefix:
        raise ValueError(f"Invalid --class {value!r}; label and match_prefix are required")
    return {"class_id": class_id, "label": label, "match_prefix": match_prefix}


def default_files(prefix: str) -> dict[str, str]:
    return {
        "completion_classes_csv": f"data/{prefix}-completion-classes.csv",
        "students_json": f"data/{prefix}-student-completion-detail.json",
        "roster_csv": f"data/{prefix}-roster.csv",
        "refunded_json": f"data/{prefix}-refunded-students.json",
        "confirmed_refunded_json": f"data/{prefix}-confirmed-refunded-students.json",
    }


def import_mcp_call(workspace: Path):
    scripts_dir = workspace / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        from build_service_todo import mcp_call  # type: ignore
    except Exception as error:
        raise RuntimeError(
            f"Cannot import DingTalk MCP helper from {scripts_dir}; "
            "run inside the codemao workspace or pass --workspace."
        ) from error
    return mcp_call


def sheet_title(sheet: dict[str, Any]) -> str:
    return str(
        sheet.get("name")
        or sheet.get("title")
        or sheet.get("sheetName")
        or sheet.get("displayName")
        or ""
    ).strip()


def sheet_id(sheet: dict[str, Any]) -> str:
    return str(sheet.get("sheetId") or sheet.get("id") or sheet.get("sheet_id") or "").strip()


def list_dingtalk_sheets(workspace: Path, node_id: str) -> list[dict[str, str]]:
    mcp_call = import_mcp_call(workspace)
    result = mcp_call("get_all_sheets", {"nodeId": node_id})
    if not result.get("success"):
        raise RuntimeError(f"Cannot list DingTalk sheets for node {node_id}: {result}")
    raw_sheets = result.get("sheets") or result.get("value") or result.get("data") or []
    sheets = []
    for item in raw_sheets:
        if isinstance(item, dict):
            sid = sheet_id(item)
            title = sheet_title(item)
            if sid or title:
                sheets.append({"sheet_id": sid or title, "name": title or sid})
    return sheets


def get_sheet_headers(workspace: Path, node_id: str, sid: str, read_range: str) -> list[str]:
    mcp_call = import_mcp_call(workspace)
    result = mcp_call("get_range", {"nodeId": node_id, "sheetId": sid, "range": read_range})
    if not result.get("success"):
        return []
    values = result.get("displayValues") or result.get("values") or []
    if not values:
        return []
    return [str(value).strip() for value in values[0]]


def header_score(headers: list[str]) -> int:
    normalized = {"".join(header.lower().split()) for header in headers}
    score = 0
    for candidate in LEARNING_HEADER_CANDIDATES:
        if "".join(candidate.lower().split()) in normalized:
            score += 1
    return score


def column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def normalize_header(value: object) -> str:
    return "".join(character for character in str(value or "").strip().lower() if not character.isspace())


def column_record(index: int, header: str) -> dict[str, Any]:
    one_based = index + 1
    return {
        "header": header,
        "index": one_based,
        "column": column_letter(one_based),
    }


def find_column(headers: list[str], aliases: tuple[str, ...]) -> dict[str, Any] | None:
    normalized_headers = [normalize_header(header) for header in headers]
    normalized_aliases = [normalize_header(alias) for alias in aliases]
    for alias in normalized_aliases:
        if alias in normalized_headers:
            index = normalized_headers.index(alias)
            return column_record(index, headers[index])
    for index, header in enumerate(normalized_headers):
        if not header:
            continue
        if any(alias and len(alias) >= 3 and alias in header for alias in normalized_aliases):
            return column_record(index, headers[index])
    return None


def detect_weekly_columns(headers: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    weekly: dict[str, dict[str, dict[str, Any]]] = {}
    patterns = (
        ("live", re.compile(r"^W(\d+).*(直播|参与)")),
        ("solitaire", re.compile(r"^W(\d+).*接龙")),
        ("completion", re.compile(r"^W(\d+).*(到课|完课)")),
    )
    for index, header in enumerate(headers):
        compact = str(header or "").replace(" ", "")
        if not compact:
            continue
        for key, pattern in patterns:
            match = pattern.search(compact)
            if match:
                week = f"W{int(match.group(1))}"
                weekly.setdefault(week, {})[key] = column_record(index, str(header))
                break
    return dict(sorted(weekly.items(), key=lambda item: int(item[0][1:])))


def detect_learning_sheet_schema(headers: list[str]) -> dict[str, Any]:
    clean_headers = [str(header or "").strip() for header in headers]
    columns: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for key, aliases in LEARNING_COLUMN_ALIASES.items():
        found = find_column(clean_headers, aliases)
        if found:
            columns[key] = found
        elif key in {"student_id", "student_name", "class_time"}:
            missing.append(key)
    weekly = detect_weekly_columns(clean_headers)
    return {
        "headers": clean_headers,
        "non_empty_headers": [header for header in clean_headers if header],
        "columns": columns,
        "weekly_columns": weekly,
        "missing_required": missing,
        "header_score": header_score(clean_headers),
    }


def inspect_learning_sheet_schema(
    args: argparse.Namespace,
    node_id: str,
    learning_sheet_id: str,
) -> dict[str, Any]:
    if not learning_sheet_id:
        return {
            "headers": [],
            "non_empty_headers": [],
            "columns": {},
            "weekly_columns": {},
            "missing_required": ["student_id", "student_name", "class_time"],
            "header_score": 0,
            "warning": "learning_sheet_id is empty; cannot inspect headers.",
        }
    try:
        headers = get_sheet_headers(
            args.workspace,
            node_id,
            learning_sheet_id,
            args.learning_sheet_range,
        )
    except Exception as error:
        return {
            "headers": [],
            "non_empty_headers": [],
            "columns": {},
            "weekly_columns": {},
            "missing_required": ["student_id", "student_name", "class_time"],
            "header_score": 0,
            "warning": f"cannot inspect learning sheet headers: {error}",
        }
    schema = detect_learning_sheet_schema(headers)
    if not headers:
        schema["warning"] = "learning sheet returned no headers."
    return schema


def resolve_learning_sheet_id(args: argparse.Namespace, node_id: str) -> str:
    explicit = args.learning_sheet_id.strip()
    if explicit and not args.validate_learning_sheet:
        return explicit
    try:
        sheets = list_dingtalk_sheets(args.workspace, node_id)
    except Exception as error:
        if explicit:
            print(
                json.dumps(
                    {
                        "warning": "cannot_validate_learning_sheet",
                        "learning_sheet_id": explicit,
                        "reason": str(error),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            return explicit
        print(
            json.dumps(
                {
                    "warning": "cannot_list_dingtalk_sheets",
                    "node_id": node_id,
                    "reason": str(error),
                    "next_step": (
                        "DingTalk access was denied or unavailable. "
                        "The CRM profile will still be generated, but learning_sheet_id "
                        "must be filled manually after the target teacher grants document access."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return ""
    if args.list_sheets:
        print(json.dumps({"node_id": node_id, "sheets": sheets}, ensure_ascii=False, indent=2))
    if explicit:
        validate_learning_sheet(args.workspace, node_id, explicit, args.learning_sheet_range)
        return explicit
    target_name = args.learning_sheet_name.strip()
    name_candidates = [target_name] if target_name else list(LEARNING_SHEET_NAME_CANDIDATES)
    for sheet in sheets:
        title = sheet["name"]
        if any(candidate and candidate in title for candidate in name_candidates):
            validate_learning_sheet(args.workspace, node_id, sheet["sheet_id"], args.learning_sheet_range)
            return sheet["sheet_id"]
    if args.auto_learning_sheet or not explicit:
        scored: list[tuple[int, str, str, list[str]]] = []
        for sheet in sheets:
            headers = get_sheet_headers(args.workspace, node_id, sheet["sheet_id"], args.learning_sheet_range)
            scored.append((header_score(headers), sheet["sheet_id"], sheet["name"], headers))
        scored.sort(reverse=True, key=lambda item: item[0])
        if scored and scored[0][0] >= 3:
            print(
                json.dumps(
                    {
                        "auto_learning_sheet": {
                            "sheet_id": scored[0][1],
                            "name": scored[0][2],
                            "score": scored[0][0],
                            "headers": scored[0][3],
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            return scored[0][1]
    raise ValueError("Cannot resolve learning sheet. Provide --learning-sheet-id or --learning-sheet-name.")


def validate_learning_sheet(workspace: Path, node_id: str, sid: str, read_range: str) -> None:
    headers = get_sheet_headers(workspace, node_id, sid, read_range)
    score = header_score(headers)
    if score < 3:
        raise RuntimeError(
            f"Sheet {sid} does not look like a learning sheet; score={score}, headers={headers}"
        )


def read_class_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            rows = payload.get("rows") or payload.get("summaries") or payload.get("classes") or []
        else:
            rows = payload
        if not isinstance(rows, list):
            raise ValueError(f"Cannot find class rows in {path}")
        return [row for row in rows if isinstance(row, dict)]
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def pick(row: dict[str, Any], *keys: str) -> str:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        if key in row and row[key] not in {None, ""}:
            return str(row[key]).strip()
        value = lowered.get(key.lower())
        if value not in {None, ""}:
            return str(value).strip()
    return ""


def infer_schedule(text: str) -> tuple[str, str]:
    compact = text.replace(" ", "")
    if "\u5468\u4e94" in compact:
        return "\u5468\u4e94\u665a", "\u5468\u4e94"
    if "\u5468\u516d" in compact and ("\u4e0b\u5348" in compact or "\u5348" in compact or "\u4e2d" in compact):
        return "\u5468\u516d\u5348", "\u5468\u516d\u5348"
    if "\u5468\u516d" in compact and ("\u665a" in compact or "\u665a\u4e0a" in compact):
        return "\u5468\u516d\u665a", "\u5468\u516d\u665a"
    if "\u5468\u65e5" in compact and ("\u4e0b\u5348" in compact or "\u5348" in compact):
        return "\u5468\u65e5\u5348", "\u5468\u65e5\u5348"
    if "\u5468\u65e5" in compact and ("\u665a" in compact or "\u665a\u4e0a" in compact):
        return "\u5468\u65e5\u665a", "\u5468\u65e5\u665a"
    return "", ""


def classes_from_file(path: Path) -> list[dict[str, Any]]:
    rows = read_class_file(path)
    package_counts = Counter(
        pick(row, "package_id", "packageId")
        for row in rows
        if pick(row, "package_id", "packageId")
    )
    primary_package = ""
    if len(package_counts) > 1 and package_counts.most_common(1)[0][1] >= 2:
        primary_package = package_counts.most_common(1)[0][0]
    classes: list[dict[str, Any]] = []
    for row in rows:
        if primary_package and pick(row, "package_id", "packageId") != primary_package:
            continue
        class_id_text = pick(row, "class_id", "classId", "班级ID", "班级id")
        try:
            class_id = int(class_id_text)
        except ValueError:
            continue
        term_name = pick(row, "term_name", "termName", "期次", "期次名", "term")
        class_name = pick(row, "class_name", "className", "班级", "班级名")
        label, match_prefix = infer_schedule(f"{term_name} {class_name}")
        if not label:
            label = class_name or str(class_id)
            match_prefix = label
        classes.append({"class_id": class_id, "label": label, "match_prefix": match_prefix})
    if not classes:
        raise ValueError(f"No classes found in {path}")
    return classes


def resolve_classes(args: argparse.Namespace) -> list[dict[str, Any]]:
    classes = [parse_class(value) for value in args.classes]
    if args.class_file:
        classes.extend(classes_from_file(args.class_file))
    deduped: dict[int, dict[str, Any]] = {}
    for item in classes:
        deduped[int(item["class_id"])] = item
    if not deduped:
        raise ValueError("Provide at least one --class or --class-file")
    return list(deduped.values())


def build_profile(args: argparse.Namespace) -> dict[str, Any]:
    prefix = args.data_prefix.strip()
    if not prefix:
        raise ValueError("--data-prefix cannot be empty")
    node_id = args.node_id.strip() or node_id_from_url(args.dingtalk_url)
    if not node_id:
        raise ValueError("Provide --node-id or a --dingtalk-url containing /nodes/<nodeId>")
    learning_sheet_id = resolve_learning_sheet_id(args, node_id)
    learning_sheet_schema = inspect_learning_sheet_schema(args, node_id, learning_sheet_id)
    files = default_files(prefix)
    overrides = {
        "completion_classes_csv": args.completion_classes_csv,
        "students_json": args.students_json,
        "roster_csv": args.roster_csv,
        "refunded_json": args.refunded_json,
        "confirmed_refunded_json": args.confirmed_refunded_json,
    }
    for key, value in overrides.items():
        if value:
            files[key] = value
    if args.class_file and not args.completion_classes_csv:
        files["completion_classes_csv"] = str(args.class_file)
    profile = {
        "data_prefix": prefix,
        "dingtalk": {
            "node_id": node_id,
            "learning_sheet_id": learning_sheet_id,
            "learning_sheet_range": args.learning_sheet_range.strip(),
            "invite_followup_sheet_name": args.invite_followup_sheet_name.strip(),
            "makeup_sheet_name": args.makeup_sheet_name.strip(),
        },
        "learning_sheet_schema": learning_sheet_schema,
        "classes": resolve_classes(args),
        "files": files,
        "wecom": {
            "enabled": True,
            "chat_id_source": "crm_capture",
            "chat_id_cache": f"data/{prefix}-wecom-parent-chat-ids.json",
            "send_result_pattern": f"data/{prefix}-week{{week}}-feedback-send-result.json",
            "require_preview_before_send": True,
            "mark_feedback_after_confirmed_send": True,
        },
    }
    if args.class_pool_id > 0:
        profile["crm"] = {"class_pool_id": args.class_pool_id}
    return profile


def update_config(path: Path, profile: dict[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if path.exists():
        config = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError(f"{path} does not contain a JSON object")
    config["profile"] = profile
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config


def main() -> int:
    args = parse_args()
    node_id = args.node_id.strip() or node_id_from_url(args.dingtalk_url)
    if args.list_sheets and not args.learning_sheet_id and not args.auto_learning_sheet and not args.classes and not args.class_file:
        if not node_id:
            raise ValueError("Provide --node-id or a --dingtalk-url containing /nodes/<nodeId>")
        sheets = list_dingtalk_sheets(args.workspace, node_id)
        print(json.dumps({"node_id": node_id, "sheets": sheets}, ensure_ascii=False, indent=2))
        return 0
    profile = build_profile(args)
    if args.update_config:
        update_config(args.update_config, profile)
    output = json.dumps(profile, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
