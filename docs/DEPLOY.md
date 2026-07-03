# ScopeLab 部署（離線安裝版 + 公開站）

ScopeLab 比照姊妹專案 **ClinScope** 的雙軌部署：

- **Track B — 離線桌面安裝版 `ScopeLab-Setup.exe`**（真正的產品）：使用者下載安裝，在
  **自己機器**上標記／訓練／推論（原生資料夾對話框、自家 GPU、寫自家硬碟才合理）。
- **Track A — 公開站 `https://scopelab.tinghaochang.com`**（展示 UI + 下載入口）：經
  Cloudflare Tunnel 把本機 `127.0.0.1:7861` 開出去，前面用 Cloudflare Access（Google 登入）擋。

> ⚠️ ScopeLab 是「標記→訓練→推論」工作台，會寫檔、會在本機 GPU 跑訓練、用**伺服器端**原生
> 對話框選資料夾 → 遠端訪客只能看 UI／範例專案，**真正能用的是下載回去的安裝版**。公開站定位
> 同 ClinScope：展示 + 下載門面。

---

## Track B — 建置離線安裝程式

前置工具：PyInstaller（建置時自動裝進 `.venv-build`）、Inno Setup 6（ISCC.exe 在
`%LOCALAPPDATA%\Programs\Inno Setup 6\`）。

```powershell
# 1) PyInstaller 打包（從乾淨 CPU-only venv；自動建 .venv-build）
powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
#    產物：packaging\dist\ScopeLab\ScopeLab.exe（onedir，~1.7 GB）

# 2) Inno Setup 編成單一安裝檔
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" packaging\scopelab.iss
#    產物：packaging\dist\ScopeLab-Setup.exe（per-user 安裝、免 admin）
```

設計重點：
- **CPU-only torch**（`requirements-runtime.txt`）：裝到誰的機器都能跑；訓練較慢、推論順，GPU 為可選升級。
- **進入點 `packaging/run_desktop.py`**：背景起 Gradio → pywebview 原生視窗（失敗退回瀏覽器）。
- **使用者資料導向 `%LOCALAPPDATA%\ScopeLab`**（`SCOPELAB_DATA_DIR`）：projects／exports／runs 不寫進
  安裝夾、重裝不丟；**首次啟動離線重生示範專案**（`make_samples`，純 PIL，寫入正確本機路徑，
  避免打包帶絕對路徑的 demo 到別人機器壞掉）。
- **離線 YOLO 基礎權重**（`yolov8s.pt`/`yolo26n.pt`）隨包，det 訓練免連網。
- 解除安裝**不**刪 `%LOCALAPPDATA%\ScopeLab`（視為使用者資料）。

冒煙測試（驗證凍結 exe 真的載入）：
```powershell
$env:SCOPELAB_DESKTOP_SMOKE="1"; $env:SCOPELAB_SMOKE_MARKER="$PWD\_smoke.json"
packaging\dist\ScopeLab\ScopeLab.exe         # 起server→寫marker→自結束
type _smoke.json                              # {"projects":..,"data_dir":..,"has_download_btn_code":true}
```

---

## Track A — 公開站

### 架構
- 網域 `tinghaochang.com`（Cloudflare Registrar）。
- **共用** tunnel `tinghaochang-sites`（原名 `clinscope`，2026-07-04 改名；UUID `e90e134d-5254-4976-8c7e-7e85a21ece98`，腳本一律用 UUID 跑）：**一個** cloudflared
  程序同時服務 clinscope(7860) + scopelab(7861) + gis(8000) + harnesshub(5178) + 根網域(5180)，ingress 見 `~/.cloudflared/config.yml`。
- Access app `scopelab` 綁單一主機名 `scopelab.tinghaochang.com`，policy = 任何 Google 帳號
  （沿用 ClinScope 的 Google IdP）。
- 公開站跑 **dev venv 的 `python app.py`**（埠 7861），權重走本機 repo。

### 一次性設定（已完成）
```powershell
# 1) config.yml ingress：scopelab.tinghaochang.com -> http://localhost:7861（已加）
# 2) DNS CNAME
cloudflared tunnel route dns tinghaochang-sites scopelab.tinghaochang.com
# 3) Startup 自啟：packaging\autostart\scopelab_public.vbs 複製到
#    %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\（只起 7861 server；tunnel 由 clinscope_public.vbs 起）
# 4) Cloudflare Access：Zero Trust 後台新建 self-hosted app `scopelab`
#    hostname=scopelab.tinghaochang.com、IdP=Google、policy=Everyone（任何 Google 真人）
# 5) 重啟共用 tunnel 套用新 ingress：packaging\autostart\restart_tunnel.bat
```

### 維運速查
| 要做的事 | 怎麼做 |
|---|---|
| 改了 app.py 要套用到公開站 | `packaging\autostart\restart_server.bat`（只重啟 7861，tunnel 不動） |
| 502 / 1033（換網路、長閒置、隧道死） | `packaging\autostart\restart_tunnel.bat`（重啟**共用** tunnel，同時救 clinscope/scopelab/gis） |
| 確認本機健康 | `Get-NetTCPConnection -LocalPort 7861 -State Listen`、`Invoke-WebRequest http://127.0.0.1:7861`（應 200） |
| 確認 Access 守門 | `curl.exe -s -o NUL -w "%{http_code}" https://scopelab.tinghaochang.com` → **302**=正常導登入 |
| 收緊檔案曝險 | 公開站設 `SCOPELAB_ALLOWED_PATHS=D:\Claude code\ScopeLab`（只服務專案夾、不曝 C:\Users；autostart 已設） |

詳見 `.claude/skills/public-tunnel`（維運）與 `.claude/skills/repackage`（重打包）。
