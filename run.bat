@echo off
REM LabelScope 啟動：沿用 ClinScope 的 venv（已含 gradio 6.17.3 / pillow / numpy），
REM 確保與 ClinScope 外觀／行為一致；若日後要獨立環境，改成本專案自己的 .venv 即可。
setlocal
set "PYEXE=D:\Claude code\ClinScope\.venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
cd /d "%~dp0"
"%PYEXE%" app.py
endlocal
