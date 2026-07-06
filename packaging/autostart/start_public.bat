@echo off
chcp 65001 >nul
REM ============================================================
REM  Start the ScopeLab public-site server on 127.0.0.1:7861.
REM  The Cloudflare tunnel is SHARED with ClinScope (one
REM  'clinscope' tunnel serves clinscope + scopelab + gis), and
REM  is started by clinscope_public.vbs. If the public URL is
REM  down, run restart_tunnel.bat once.
REM ============================================================
set SCOPELAB_PORT=7861
set SCOPELAB_PUBLIC=1
set PYTHONIOENCODING=utf-8
REM 收緊檔案曝險：公開站只服務 ScopeLab 專案資料夾（不曝整個 D:\ / C:\Users）
set "SCOPELAB_ALLOWED_PATHS=D:\Claude code\ScopeLab"
REM 站內 Google 登入祕密（git-ignored）。無此檔則不啟用登入（訪客唯讀）。
if exist "%~dp0env.local.bat" call "%~dp0env.local.bat"

echo [ScopeLab] Starting server on 127.0.0.1:7861 ...
start "ScopeLab Server" "D:\Claude code\ClinScope\.venv\Scripts\python.exe" -u "D:\Claude code\ScopeLab\app.py"

echo.
echo Done. Public URL (shared 'clinscope' tunnel): https://scopelab.tinghaochang.com
echo If it shows 502/1033, run restart_tunnel.bat once (restarts the shared tunnel).
echo (Close the new window to stop the ScopeLab server.)
