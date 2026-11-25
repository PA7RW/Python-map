@echo off
title Military Calisthenics Workout
cd /d "%~dp0"
call .venv\Scripts\python.exe workout_app.py
pause
