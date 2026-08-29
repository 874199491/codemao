#!/usr/bin/env python3
"""Ensure optional monthly-exam feedback dependencies are importable."""
from __future__ import annotations

import importlib
import subprocess
import sys


REQUIRED = [
    ("requests", "requests"),
    ("fpdf", "fpdf2"),
    ("pptx", "python-pptx"),
    ("win32com.client", "pywin32"),
    ("winpty", "pywinpty"),
]

PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"


def missing_packages() -> list[str]:
    missing: list[str] = []
    for module_name, package_name in REQUIRED:
        try:
            importlib.import_module(module_name)
        except Exception:
            missing.append(package_name)
    return list(dict.fromkeys(missing))


def main() -> int:
    missing = missing_packages()
    if not missing:
        print("Monthly exam dependencies are ready.")
        return 0
    print("Installing missing monthly exam dependencies: " + ", ".join(missing), flush=True)
    command = [sys.executable, "-m", "pip", "install", "--user", "-i", PIP_INDEX, *missing]
    result = subprocess.run(command)
    if result.returncode != 0:
        # 清华镜像失败时回退默认源
        print("Tsinghua mirror failed, retrying with default PyPI...", flush=True)
        command = [sys.executable, "-m", "pip", "install", "--user", *missing]
        result = subprocess.run(command)
    if result.returncode != 0:
        return result.returncode
    remaining = missing_packages()
    if remaining:
        print("Still missing after install: " + ", ".join(remaining), file=sys.stderr)
        return 1
    print("Monthly exam dependencies installed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
