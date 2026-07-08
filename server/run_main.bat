@echo off

start cmd /k py -m uvicorn main:app --reload --port 8764 --host 0.0.0.0
timeout /t 3 >nul
start cmd /k ngrok http 8764

pause