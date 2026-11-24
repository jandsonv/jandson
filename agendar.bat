@echo off
rem Agenda o robo para rodar a cada 3 horas no Agendador de Tarefas do Windows.
schtasks /Create /TN "RoboPassagens" /TR "\"%~dp0rodar.bat\"" /SC HOURLY /MO 3 /F
echo.
echo Para remover: schtasks /Delete /TN "RoboPassagens" /F
pause
