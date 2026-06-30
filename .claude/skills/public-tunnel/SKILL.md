---
name: public-tunnel
description: 維護/部署 ScopeLab 公開網址 scopelab.tinghaochang.com(Cloudflare Tunnel + Access)。觸發詞:公開站連不上、502 Bad gateway、Host Error、錯誤 1033、重啟 tunnel、開/關公開站、改誰能登入、Access policy、IdP/Google 登入、換網路後斷線、開機自啟。⚠️ tunnel 與 ClinScope 共用(同一個 clinscope tunnel 服務 clinscope+scopelab+gis)。涉及 ~/.cloudflared/config.yml、packaging/autostart/(start_public/restart_server/restart_tunnel.bat、scopelab_public.vbs)、app.py launch_app() 的 SCOPELAB_AUTH_USER/PASS + SCOPELAB_ALLOWED_PATHS、Cloudflare Zero Trust(one.dash.cloudflare.com)。完整背景見 docs/DEPLOY.md Track A。
---

# Public Tunnel — ScopeLab 公開站維運

把本機 server(`127.0.0.1:7861`)經 Cloudflare Tunnel 開成 `https://scopelab.tinghaochang.com`,前面用
Cloudflare Access(Google 登入)擋。**本機是真正在跑的地方,Cloudflare 只是轉送** → 電腦關機 / server
沒跑 / cloudflared 沒跑就連不上。完整架構見 `docs/DEPLOY.md`(Track A);本 skill 給維運速查 + 坑。

## 現況關鍵值
- 網域 `tinghaochang.com`(Cloudflare Registrar)。
- **⚠️ tunnel 與 ClinScope 共用**:tunnel 名 `clinscope`、UUID `e90e134d-5254-4976-8c7e-7e85a21ece98`。
  **一個** cloudflared 程序同時服務 clinscope(7860) + scopelab(7861) + gis(8000)。
  重啟 tunnel = 同時影響三個站。
- Access app `scopelab`,destination=`scopelab.tinghaochang.com`(**單一主機名**),policy `任何登入者`=Everyone,
  登入=**Google OAuth IdP**(沿用 ClinScope 那個,任何 Google 真人可進、擋 bot)。
- 公開站跑 **dev venv 的 `python app.py`**(埠 7861)。
- 開機自啟:`Startup\scopelab_public.vbs`(只起 7861 server;tunnel 由 `clinscope_public.vbs` 起)。
- `cloudflared.exe` 在 `C:\Program Files (x86)\cloudflared\`(無 admin → 不能裝 service,用 Startup .vbs 取代)。

## 維運速查
| 要做的事 | 怎麼做 |
|---|---|
| **改了 app.py 要套用到公開站**(版面/CSS/JS) | `packaging\autostart\restart_server.bat`(只重啟 7861、tunnel 不動)。**在 session 內由 Claude 重啟需 detached**:`Start-Process wscript.exe -ArgumentList '"<Startup\scopelab_public.vbs>"'`(VBS 須**純 ASCII**,含中文存 UTF-8 會被 wscript 當 ANSI 解析失敗而靜默不啟動)。 |
| **502 Bad gateway / Host Error**(換網路、睡醒後) | `packaging\autostart\restart_tunnel.bat`(重啟**共用** tunnel)。根因:cloudflared 連線卡舊網路;本機 7861 仍 200。**注意:同時重啟 clinscope/gis**。 |
| **錯誤 1033 / Cloudflare 隧道錯誤頁**(≠502) | DNS 指向 Cloudflare 但**沒有 cloudflared 連上**——通常 cloudflared 程序整個不在了(長閒置被系統回收、或重開機後自啟沒成功)。跑 `restart_tunnel.bat` 重起共用 tunnel;若連 7861 server 也死,先 `restart_server.bat`(或跑 Startup 的 scopelab_public.vbs)。判別見「確認本機健康」。 |
| **手動開公開站(server 部分)** | `packaging\autostart\start_public.bat`(可見視窗,好看 log;只起 7861 server) |
| **停掉 ScopeLab server** | 工作管理員結束佔 7861 的 `python.exe`;或刪 Startup 的 `scopelab_public.vbs` 取消自啟(不影響 clinscope/gis) |
| **確認本機健康** | `Get-NetTCPConnection -LocalPort 7861 -State Listen`、`Get-Process cloudflared`、`Invoke-WebRequest http://127.0.0.1:7861`(應 200) |
| **確認 Access 守門** | `curl.exe -s -o NUL -w "%{http_code}" https://scopelab.tinghaochang.com` → **302**=正常(導向 Access 登入頁);**200 直接進站**=Access 失效要查;**502**=回源斷;**530 + 錯誤 1033**=cloudflared 沒連上(多半 tunnel 死) |
| **改誰能登入** | one.dash.cloudflare.com → Access → Applications → `scopelab` → Policies → 編輯 `任何登入者`:`Everyone`=任何真人 / 改 `Emails` 白名單=限特定人 |
| **改登入方式** | Access → Applications → `scopelab` → Authentication(關/開 IdP) |
| **收緊檔案曝險** | 自啟/啟動環境設 `SCOPELAB_ALLOWED_PATHS=D:\Claude code\ScopeLab`(只服務專案夾、不曝 C:\Users;autostart 已設);亦可 `SCOPELAB_AUTH_USER/PASS` 加 Gradio 密碼(Access 已是更強的邊緣驗證) |

## 坑(都實際踩過)
- **tunnel 是共用的**:scopelab 沒有自己的 tunnel,掛在 ClinScope 的 `clinscope` 上(config.yml 多一條 ingress)。
  好處是一個程序顧三站;代價是**重啟 tunnel 會同時斷 clinscope/gis 約 10 秒**。改 config.yml 後現有 tunnel
  不會自動 reload,要 `restart_tunnel.bat` 重啟才套用新 ingress。
- **無 admin → 不能 `cloudflared service install`** → 用 Startup 的 .vbs 登入自啟(detached、隱藏)。
- **換網路必斷**:WiFi 切換/睡眠喚醒後 cloudflared 卡舊連線 → 502。重啟共用 tunnel 即解。
- **Access 只綁單一主機名**:`scopelab` app 只保護 `scopelab.tinghaochang.com`。
- **detached vs 背景任務**:run_in_background 起的 server 會隨 session 結束被砍;要常駐請走 `.vbs`(wscript)。
- **ScopeLab 不像 ClinScope 有雲端權重**:沒有 weights manifest / HF 下載這層,公開站直接跑 repo;故無「選模型才下載」橫幅。
- **公開站的「選資料夾」原生對話框開在伺服器端、訓練吃本機 GPU** → 遠端訪客只能看 UI/範例,真正能用的是下載安裝版。
