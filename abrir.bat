@echo off
cd /d "%~dp0"
start "" http://localhost:8000
.venv\Scripts\python -m robo app
pause
