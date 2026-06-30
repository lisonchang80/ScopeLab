# build_exe.ps1 — 從乾淨 CPU-only venv 用 PyInstaller 打包 ScopeLab 桌面 exe。
# 產物：packaging\dist\ScopeLab\ScopeLab.exe（onedir）。再用 ISCC 編 scopelab.iss → ScopeLab-Setup.exe。
#
# 用法（於 repo 根）：  powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $PSScriptRoot      # 本檔在 packaging\ → repo 根
Set-Location $ROOT

$buildPy = ".\.venv-build\Scripts\python.exe"
if (-not (Test-Path $buildPy)) {
    Write-Host "[build] 由 ClinScope dev venv 衍生乾淨 venv .venv-build（不含 dev 套件）..."
    & "D:\Claude code\ClinScope\.venv\Scripts\python.exe" -m venv .venv-build
}

Write-Host "[build] 安裝 runtime 依賴（CPU torch wheel index）+ pyinstaller ..."
& $buildPy -m pip install --upgrade pip
& $buildPy -m pip install -r packaging\requirements-runtime.txt
& $buildPy -m pip install pyinstaller

Write-Host "[build] PyInstaller 打包（--clean）..."
& $buildPy -m PyInstaller --noconfirm --clean packaging\scopelab.spec `
    --distpath packaging\dist --workpath packaging\build

Write-Host "[build] 完成 → packaging\dist\ScopeLab\ScopeLab.exe"
Write-Host "[build] 下一步：用 ISCC 編譯安裝程式："
Write-Host '        & "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" packaging\scopelab.iss'
