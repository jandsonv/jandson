@echo off
cd /d "%~dp0"
if not exist dados mkdir dados
.venv\Scripts\python -m robo rodar %* >> dados\log.txt 2>&1
