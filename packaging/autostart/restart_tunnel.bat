@echo off
chcp 65001 >nul
REM ============================================================
REM  Restart the SHARED Cloudflare tunnel 'tinghaochang-sites'.
REM  This single tunnel serves gis + scopelab + harnesshub +
REM  tinghaochang.com (and clinscope when that server runs), so
REM  restarting it recovers ALL those public sites after a
REM  network change (WiFi switch, hotspot, sleep/wake) or a
REM  502 / 1033 error. The local servers are left untouched.
REM  Only the shared tunnel's cloudflared is stopped: other
REM  tunnels on this PC (e.g. a cloudflared started with its own
REM  --config file) keep running.
REM ============================================================
echo Stopping the old shared-tunnel cloudflared (stale network connections) ...
REM Match = command line contains the tunnel UUID (from the "tunnel:" key of the local
REM config.yml, as Startup\cloudflared_tunnel.vbs runs it) or the default config.yml path
REM (as this script runs it). Never "taskkill /IM cloudflared.exe": that kills every tunnel.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$cfg = Join-Path $env:USERPROFILE '.cloudflared\config.yml'; $id = ''; if (Test-Path $cfg) { $m = Select-String -Path $cfg -Pattern '^\s*tunnel:\s*(\S+)' | Select-Object -First 1; if ($m) { $id = $m.Matches[0].Groups[1].Value } }; $hit = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cloudflared.exe' } | Where-Object { $c = [string]$_.CommandLine; ($id -ne '' -and $c.ToLower().Contains($id.ToLower())) -or $c.ToLower().Contains('\.cloudflared\config.yml') }); foreach ($p in $hit) { Write-Host ('  stopping PID ' + $p.ProcessId); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }; if ($hit.Count -eq 0) { Write-Host '  (shared tunnel was not running)' }"
timeout /t 2 /nobreak >nul
echo Starting fresh shared tunnel on the current network ...
REM Run by UUID (not name) so renaming the tunnel can never break this script.
REM The UUID is read from the "tunnel:" key of the local config.yml, so it never lands in git (this repo is public).
start "Shared Tunnel (tinghaochang-sites)" "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --config "%USERPROFILE%\.cloudflared\config.yml" run
echo.
echo Done. Wait ~10s, then reload https://scopelab.tinghaochang.com
echo (Keep the new window open; closing it stops the shared tunnel for ALL its sites.)
