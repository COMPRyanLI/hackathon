@echo off
REM Start GradeLens on http://127.0.0.1:8000 (loopback only).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
