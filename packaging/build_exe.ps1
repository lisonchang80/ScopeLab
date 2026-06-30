# build_exe.ps1 - Build the ScopeLab desktop exe from a clean CPU-only venv with PyInstaller.
# Output: packaging\dist\ScopeLab\ScopeLab.exe (onedir). Then compile scopelab.iss with ISCC
# to produce ScopeLab-Setup.exe.
#
# NOTE: kept ASCII-only on purpose -- Windows PowerShell 5.1 reads a no-BOM UTF-8 .ps1 as ANSI
#       and mis-parses non-ASCII characters.
#
# Usage (from repo root):  powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $PSScriptRoot      # this file is in packaging\ -> repo root
Set-Location $ROOT

$buildPy = ".\.venv-build\Scripts\python.exe"
if (-not (Test-Path $buildPy)) {
    Write-Host "[build] Creating clean .venv-build from ClinScope dev venv interpreter..."
    & "D:\Claude code\ClinScope\.venv\Scripts\python.exe" -m venv .venv-build
}

Write-Host "[build] Installing runtime deps (CPU torch wheel index) + pyinstaller..."
& $buildPy -m pip install --upgrade pip
& $buildPy -m pip install -r packaging\requirements-runtime.txt
& $buildPy -m pip install pyinstaller

Write-Host "[build] PyInstaller packaging (--clean)..."
& $buildPy -m PyInstaller --noconfirm --clean packaging\scopelab.spec --distpath packaging\dist --workpath packaging\build

Write-Host "[build] Done -> packaging\dist\ScopeLab\ScopeLab.exe"
Write-Host "[build] Next: compile the installer with ISCC, e.g.:"
Write-Host '        & "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" packaging\scopelab.iss'
