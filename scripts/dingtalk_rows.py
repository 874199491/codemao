#!/usr/bin/env python3
"""Helpers for normalizing DingTalk sheet range rows."""

from __future__ import annotations

from typing import Any


def sanitize_rows(values: object) -> list[list[Any]]:
    rows: list[list[Any]] = []
    if not isinstance(values, list):
        return rows
    for row in values:
        if row is None:
            continue
        if isinstance(row, list):
            rows.append(row)
        else:
            rows.append([row])
    return rows


def result_rows(result: dict[str, Any], *, source: str = "钉钉表格") -> list[list[Any]]:
    raw = result.get("displayValues") or result.get("values") or result.get("data") or []
    rows = sanitize_rows(raw)
    while rows and not any(str(value or "").strip() for value in rows[0]):
        rows.pop(0)
    return rows


def header_row(values: list[list[Any]], *, source: str = "钉钉表格") -> list[str]:
    rows = sanitize_rows(values)
    while rows and not any(str(value or "").strip() for value in rows[0]):
        rows.pop(0)
    if not rows:
        raise RuntimeError(f"无法读取{source}表头：返回为空")
    return [str(value).strip() if value is not None else "" for value in rows[0]]
