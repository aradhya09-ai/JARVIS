@echo off
rem Starts the JARVIS backend on the loopback address and keeps the log on disk,
rem so a session that was launched at logon can still be diagnosed afterwards.
cd /d "%~dp0"
if not exist logs mkdir logs
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8765 >> logs\autostart.log 2>&1
