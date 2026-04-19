@echo off
title Discord Signal Tracker
echo Starting Discord Signal Tracker...
echo.

cd /d "%~dp0"

echo [1/2] Starting web dashboard on http://localhost:8099/dashboard.html
start "Dashboard Server" cmd /c "python -m http.server 8099"

echo [2/2] Starting scraper in watch mode (polling every 5 min)
echo      Press Ctrl+C to stop.
echo.
python scraper.py --watch
