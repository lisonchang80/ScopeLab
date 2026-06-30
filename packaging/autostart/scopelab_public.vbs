' ScopeLab public site - logon autostart (server only; hidden, detached).
' Starts the ScopeLab Gradio server on 127.0.0.1:7861.
' The Cloudflare tunnel is SHARED and started by clinscope_public.vbs
' (one 'clinscope' tunnel serves clinscope + scopelab + gis).
' NOTE: keep this file PURE ASCII -- a VBS with non-ASCII saved as UTF-8
' is mis-parsed by wscript (ANSI) and silently fails to launch.
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd /c set SCOPELAB_PORT=7861&& set SCOPELAB_PUBLIC=1&& set ""SCOPELAB_ALLOWED_PATHS=D:\Claude code\ScopeLab""&& ""D:\Claude code\ClinScope\.venv\Scripts\python.exe"" -u ""D:\Claude code\ScopeLab\app.py""", 0, False
