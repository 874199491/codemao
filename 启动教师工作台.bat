@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY_CMD="
for %%V in (-3.14 -3.13 -3.12 -3.11 -3.10) do (
  py %%V -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) and sys.prefix == sys.base_prefix else 1)" >nul 2>nul
  if not errorlevel 1 if not defined PY_CMD set "PY_CMD=py %%V"
)
if not defined PY_CMD (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) and sys.prefix == sys.base_prefix else 1)" >nul 2>nul
  if not errorlevel 1 set "PY_CMD=python"
)
if not defined PY_CMD (
  python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) and sys.prefix == sys.base_prefix else 1)" >nul 2>nul
  if not errorlevel 1 set "PY_CMD=python3"
)
if not defined PY_CMD (
  echo 未找到 Python 3.10+，请先运行“一键安装运行环境.bat”。
  pause
  exit /b 1
)
%PY_CMD% apps\teacher_workbench\server.py --host 127.0.0.1 --port 8876
pause
