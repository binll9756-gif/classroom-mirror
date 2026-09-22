@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo Starting Kejing CLI ...
echo (type /help for commands, /exit to quit)
echo.
python run_teach.py
pause
