@echo off
chcp 65001 >nul
REM ============================================================
REM  Restart the SHARED Cloudflare tunnel 'tinghaochang-sites'.
REM  This single tunnel serves clinscope + scopelab + gis, so
REM  restarting it recovers ALL three public sites after a
REM  network change (WiFi switch, hotspot, sleep/wake) or a
REM  502 / 1033 error. The local servers on :7860/:7861/:8000
REM  are left running untouched.
REM ============================================================
echo Stopping old cloudflared (stale network connections) ...
taskkill /IM cloudflared.exe /F >nul 2>&1
timeout /t 2 /nobreak >nul
echo Starting fresh shared tunnel on the current network ...
REM Run by UUID (not name) so renaming the tunnel can never break this script.
start "Shared Tunnel (tinghaochang-sites)" "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel run e90e134d-5254-4976-8c7e-7e85a21ece98
echo.
echo Done. Wait ~10s, then reload https://scopelab.tinghaochang.com
echo (Keep this window open; closing it stops the tunnel for ALL sites.)
