@echo off
cd /d "%~dp0"
py -3 -m venv .venv || goto :erro
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt || goto :erro
if not exist .env copy .env.example .env
echo.
echo Instalado. Agora preencha o arquivo .env e rode: rodar.bat
pause
exit /b 0
:erro
echo Falhou. Confira se o Python esta instalado (python.org) e tente de novo.
pause
exit /b 1
