@echo off
chcp 65001 >nul
REM ============================================================
REM  Restart ONLY the ScopeLab server (port 7861) after you
REM  change app.py / src. The shared Cloudflare tunnel is left
REM  running untouched (it auto-recovers once 7861 is back).
REM    - Public URL 502/1033 (network change)? -> restart_tunnel.bat
REM ============================================================
echo Stopping old ScopeLab server on :7861 ...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":7861" ^| findstr LISTENING') do taskkill /F /PID %%p >nul 2>&1
timeout /t 1 /nobreak >nul
echo Starting fresh ScopeLab server ...
set SCOPELAB_PORT=7861
set SCOPELAB_PUBLIC=1
set PYTHONIOENCODING=utf-8
set "SCOPELAB_ALLOWED_PATHS=D:\Claude code\ScopeLab"
REM 站內 Google 登入祕密（git-ignored）。無此檔則不啟用登入（訪客唯讀）。
if exist "%~dp0env.local.bat" call "%~dp0env.local.bat"
start "ScopeLab Server" "D:\Claude code\ClinScope\.venv\Scripts\python.exe" -u "D:\Claude code\ScopeLab\app.py"
echo.
echo Done. Wait ~10s for it to load, then reload https://scopelab.tinghaochang.com
echo (Keep this window open; closing it stops the server.)
