@echo off
cd /d "%~dp0"
py -3.10 apps\teacher_workbench\server.py --host 127.0.0.1 --port 8876
pause
