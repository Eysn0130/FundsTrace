@echo off
title FundsTrace
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Create the development environment first. See README.md.
  echo uv venv --python 3.11
  echo uv pip sync requirements-dev.lock
  exit /b 1
)
".venv\Scripts\python.exe" run.py
