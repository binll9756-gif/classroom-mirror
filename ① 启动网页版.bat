@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo Starting Kejing Web UI ...
echo (Chinese banner below; open http://127.0.0.1:8000 after it starts)
echo.
python run_web.py
echo.
echo Server stopped. If you saw an error above, please copy it and send to the team.
pause
