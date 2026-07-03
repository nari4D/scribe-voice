@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting Scribe... (hold Right Ctrl to talk, release to type)
echo Close this window or press Ctrl+C to stop.
python scribe_realtime.py
echo.
echo === Scribe stopped ===
pause
