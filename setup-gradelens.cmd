@echo off
REM One-time setup: Python venv, pinned packages, frontend build, .env.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
