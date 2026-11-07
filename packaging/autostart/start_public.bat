@echo off
chcp 65001 >nul
REM ============================================================
REM  Start the ScopeLab public-site server on 127.0.0.1:7861.
REM  The Cloudflare tunnel is SHARED with the other sites (one
REM  'tinghaochang-sites' tunnel serves clinscope + scopelab + gis
REM  + harnesshub + portfolio). It is started at logon by
REM  Startup\cloudflared_tunnel.vbs (split out of clinscope_public.vbs
REM  on 2026-08-07); this launcher does not start it. If the public
REM  URL shows error 1033 (no cloudflared connected), run that VBS.
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
echo Done. Public URL (shared 'tinghaochang-sites' tunnel): https://scopelab.tinghaochang.com
echo If it shows 1033, the shared tunnel is down: run Startup\cloudflared_tunnel.vbs.
echo (Close the new window to stop the ScopeLab server.)
