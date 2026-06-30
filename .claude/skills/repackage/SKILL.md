---
name: repackage
description: 把 ScopeLab 重新打包/發佈成桌面安裝程式 ScopeLab-Setup.exe。改了 app.py / src / packaging 後要重出安裝檔時用。觸發詞:重新打包、重發佈、更新安裝程式、rebuild installer、repackage、出新版安裝檔、改了介面要更新桌面版。涉及 packaging/(build_exe.ps1/scopelab.spec/scopelab.iss/run_desktop.py/requirements-runtime.txt)、app.py、src/。背景與決策見 docs/DEPLOY.md(Track B)。
---

# Repackage — 重新打包 ScopeLab 桌面安裝版

改了前端/邏輯後,重出 `ScopeLab-Setup.exe`。ScopeLab **沒有雲端權重**(使用者自己訓練模型),所以比
ClinScope 簡單——不必更新 weights manifest、不必傳 HuggingFace,只要重打包即可。

## 兩步驟
```powershell
# 1) PyInstaller 打包(從乾淨 CPU-only .venv-build;首次會自動建)
powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
#    產物：packaging\dist\ScopeLab\ScopeLab.exe(onedir ~1.7 GB)

# 2) Inno Setup 編成單一安裝檔
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" packaging\scopelab.iss
#    產物：packaging\dist\ScopeLab-Setup.exe(per-user、免 admin)
```

## 冒煙測試(每次重打包後驗一次)
```powershell
$env:SCOPELAB_DESKTOP_SMOKE="1"; $env:SCOPELAB_SMOKE_MARKER="$PWD\_smoke.json"
packaging\dist\ScopeLab\ScopeLab.exe        # 起server→寫marker→自結束(os._exit)
type _smoke.json                             # 應見 {"projects":..,"has_download_btn_code":true},無 "error"
Remove-Item Env:\SCOPELAB_DESKTOP_SMOKE,Env:\SCOPELAB_SMOKE_MARKER
```
marker 有 `"error"` = 凍結後缺 import → 多半要在 `packaging/scopelab.spec` 補 `collect_all`/`hiddenimports`
(尤其函式內 lazy import 的套件)。

## 何時要動哪個檔
| 改了什麼 | 要不要 / 怎麼動 packaging |
|---|---|
| app.py / src 的程式邏輯、CSS、JS | 不必改 packaging,直接跑上面兩步(app.py 與整個 src 樹以 data 檔隨包) |
| 新增 import 一個**第三方套件** | 在 `scopelab.spec` 的 `_add(...)` 清單或 `hiddenimports` 補上,再重打包 |
| 新增一支 `src/**/xxx.py` 子模組 | 不必改(spec 用 `rglob("*.py")` 收整個 src 樹);若是**函式內 lazy import** 且 smoke 出 error 才補 hiddenimports |
| 換 YOLO 基礎權重 / 加離線權重 | 把 `*.pt` 放 repo 根,spec 的 `for _w in (...)` 會帶進去 |
| 改版本號 / 安裝行為 / 捷徑 | 改 `scopelab.iss`(AppVer、[Tasks]、[Icons]、[Run]) |
| 改桌面視窗標題 / 啟動流程 / 資料夾種子 | 改 `packaging/run_desktop.py` |

## 坑
- **必用乾淨 CPU venv**:`build_exe.ps1` 衍生 `.venv-build` 裝 CPU torch;勿直接用 dev cu124 venv,否則把 ~2.5GB
  CUDA runtime 打進安裝包(文書機用不到)。
- **PowerShell 腳本(.ps1)保持純 ASCII**:Windows PowerShell 5.1 把無 BOM 的 UTF-8 .ps1 當 ANSI 讀,
  中文會 parse error。`build_exe.ps1` 刻意全英文。
- **使用者資料在 `%LOCALAPPDATA%\ScopeLab`**:重裝/升級不會動到使用者的 projects/runs/exports;
  解除安裝也不刪(視為使用者資料)。
- **首次啟動才種 demo**:`run_desktop._seed_demos()` 只在資料夾沒專案時用 `make_samples` 離線重生
  (寫正確本機路徑);已有專案不覆蓋。
- 發佈前提醒使用者:公開站「⬇下載桌面版」鈕是讀 `packaging/dist/ScopeLab-Setup.exe`,重打包後**新檔即時生效**
  (檔名不變),不必動 app.py。
