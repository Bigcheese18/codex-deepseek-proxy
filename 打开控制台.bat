@echo off
cd /d C:\Users\21238\Desktop\Codex
netstat -ano | findstr "127.0.0.1:38441" >nul 2>&1
if %errorlevel% neq 0 (
    echo Starting...
    start /B C:\Users\21238\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe dashboard.py
    timeout /t 4 /nobreak >nul
)
start "" http://127.0.0.1:38441
