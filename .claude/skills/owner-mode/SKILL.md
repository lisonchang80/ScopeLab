---
name: owner-mode
description: 給 Gradio 公開站加「站主模式」——站內 Google 登入 + 站主可完整操作 / 訪客唯讀（按鈕虛化）+「本地模式」開關。觸發詞：站主模式、owner mode、公開站登入、Google 登入、訪客唯讀、按鈕虛化、本地模式、公開站可操作/唯讀、把限制改成登入制。涉及 src/auth.py、app.py（build/launch_app/handlers）、src/train/tab.py、src/infer/tab.py、packaging/autostart/(env.local.bat/*.bat/*.vbs)、.gitignore；外加 GCP OAuth 授權來源 + Cloudflare Access Bypass。
---

# 公開站「站主模式」（in-app Google 登入 + 站主/訪客閘門）

把公開站從「靜態 PUBLIC 全擋」升級成：**匿名路人唯讀瀏覽 + 站內 Google 登入 + 只有站主（OWNER_EMAIL）可操作**。
ClinScope 先做、ScopeLab 移植（2026-07-07）。未來 GIS/Portfolio/MatScope 同理。這是**跨檔多步**，照下面順序做。

## 架構（權威 = 後端 guard；UI 虛化只是第一層）

- **身分**：前端 GSI → ID token(JWT) → 後端 `verify_oauth2_token` 驗簽/aud/iss → 簽 itsdangerous cookie 記 email。
- **站主**：`is_owner(request)` = 非 PUBLIC(桌面/dev) 恆真；PUBLIC 時 `identity_email==OWNER_EMAIL`（fail-closed，匿名/不符=訪客）。
- **本機操作閘門**：`public_locked(request)` = `PUBLIC and (not is_owner or not 本地模式)`。桌面/dev 永不擋。
- **本地模式**：程序層級全域旗標。**站主一登入即自動開**（`identity_bar` 內 `set_local_mode(True)`）→ 限制立即解除；站主可手動取消 = 預覽訪客唯讀。
- **UI**：`identity_bar` 出身分列 + 站主才顯示的 checkbox；`owner_button_gate` 把「本機操作」鈕對訪客虛化（`interactive=False`）。

## 檔案清單（ScopeLab 實例）

- **`src/auth.py`**（新模組，所有邏輯集中這）：常數(OWNER_EMAIL/GOOGLE_CLIENT_ID/SESSION_SECRET/COOKIE_NAME/AUTH_ENABLED)、`make/read_session`、`verify_google_credential`、`_cookie_email/_req_email/identity_email`、`is_owner`、`set_local_mode/public_locked/locked_msg`、`guard_owner`、`add_auth_routes(POST /auth/google + /auth/logout)`、`identity_bar`、`owner_button_gate`、`AUTH_JS`(GSI 前端)。
- **`app.py`**：`from src import ... auth`；build() 加 `identity_html`(elem_id `sl-identity`) + `local_chk`(elem_id `sl-local`, visible=False)；被擋 handler 改 `if auth.public_locked(request): raise gr.Error(auth.locked_msg(request))`（只寫 session、不寫磁碟的事件可改成回原值不落地；刪除等破壞性事件先跟其他寫入一樣檢查 `public_locked`（站主預覽訪客時也擋，審查 SL-6），再用 `auth.guard_owner(request)` 當第二道）；`if store.PUBLIC:` 段掛 `demo.load(auth.identity_bar,…)` + `_gate_btns`(owner_button_gate) + `local_chk.change(_toggle_local,…)` + `AUTH_JS`；`launch_app` 在 `auth.AUTH_ENABLED` 時走 `mount_gradio_app(FastAPI)+uvicorn`（掛 `/auth/*`），否則原 `demo.launch`。CSS 加 `.sl-idbar/.sl-owner/.sl-guest/.sl-authbtn/.sl-gbtn/.sl-localchk` + `#sl-identity:empty{display:none}`。
- **`src/train/tab.py` / `src/infer/tab.py`**：被擋 handler 加 `request: gr.Request=None` + `if auth.public_locked(request)`；build 函式**多回傳動作鈕**供 app.py 虛化（train +train_btn/detect_btn；infer +pick/infer/single/save/csv）。「🔍 偵測裝置」也要擋：它會 import torch 並在網站程序建 CUDA context。
- **launchers**：`*.bat`/`*.vbs` 載入 git-ignored `env.local.bat`（`if exist … call …`）。
- **`.gitignore`**：`packaging/autostart/env.local.bat`、`identity_debug.log`。

## ⚠️ 致命坑：`from __future__ import annotations` + FastAPI Request → 422

`auth.py` 有 `from __future__ import annotations` 時，`async def _auth_google(request: Request)` 的註解變**字串** `'Request'`。
FastAPI 用 `get_type_hints()` 在**模組 globals** 解析——若 `Request` 是 `add_auth_routes` 內的 **local import**，模組 globals 找不到 → FastAPI 把它當 **query 參數** → 每次登入回 **422**（body `{"detail":[{"loc":["query","request"]…}]}`），前端顯示「登入失敗」。
**修法**：`from starlette.requests import Request` 放**模組頂層**（不是函式內）。
> ClinScope app.py **沒有** future annotations（註解是真物件）故不受影響——別誤以為兩邊一樣。
> 快速自測：`TestClient(app).post('/auth/google', json={'credential':'x.y'})` 應回 **401**（不是 422）。

## ⚠️ 全域本地模式旗標的競態

`identity_bar` 對**非站主不要** `set_local_mode(False)`——否則路人一載入就把全域旗標關掉，會把已登入站主一起鎖掉（`public_locked` 對非站主本就恆擋，不需靠旗標）。只在 `if owner: set_local_mode(True)`。

## 設計教訓：站主登入即解鎖，別把本地模式當硬閘門

第一版把「本地模式」設成不勾就全擋 → 站主登入後仍撞「下載桌面版」限制（使用者不滿）。
正解：**站主登入自動開本地模式**（`identity_bar` 設 checkbox value=True，`.change` 觸發 `_toggle_local` 重啟用鈕）；checkbox 變**預覽開關**（取消=預覽訪客唯讀）。`locked_msg` 分流：訪客→「下載桌面版」；站主預覽→「勾回本地模式」。
> `identity_bar` 設 checkbox value=True 會觸發 `.change` → `_toggle_local` 只在「關閉(預覽)」時 `gr.Info`，開啟不跳（免每次載入都 toast）。

## 兩個雲端外部步驟（可用 claude-in-chrome 驅動）

1. **GCP OAuth 授權 JS 來源**：把 `https://<sub>.tinghaochang.com` 加進 client 的「已授權的 JavaScript 來源」。
   - 可**沿用 ClinScope 的 OAuth client**（`254767953014-l8nce…`，GCP 專案 plenary-treat-288305）多加一個來源即可；GSI 按鈕能 render 出來就代表來源已生效。
2. **Cloudflare Access → 開放匿名**：One → Access → Applications → 該 app → Policies → **Add existing policy** 選 `clinscope-public-bypass`(Bypass/Everyone)。Bypass 政策 app-agnostic 可共用，免自己打字進 CF security config（那區常被安全層擋）。驗證：`curl` 公開網址回 **200**（非 302）。
> 沒改 CF 成 Bypass 前，站仍在 Access 後。`cf-access-authenticated-user-email` 標頭**預設不採信**（`identity_email` 只在顯式設 `SCOPELAB_TRUST_CF_ACCESS=1` 時才交給 `_req_email`）：只有 app 仍擺在 Access 強制登入後的過渡部署才設；**Bypass 下絕不設**，因為任何經 tunnel 的請求都能自帶這個標頭冒充站主。沒設旗標時，站主要走站內 Google 登入才會被認出來。

## PR #1（8cc46b2）／34f9302 建立的安全不變式（移植時一條都不能少）

- **閘門在 handler 內**：每個寫磁碟、吃 GPU、開原生對話框、刪除的事件函式自己檢查 `public_locked`／`guard_owner`。訪客可以繞過 UI 直接 POST `/gradio_api/queue/join` 觸發任何事件，按鈕虛化擋不住。
- **`TRUST_CF_ACCESS` 預設關**：見上面 Cloudflare 那段，Bypass 下絕不設。
- **`SESSION_SECRET` fail-closed**：公開站沒設金鑰或用 repo 內的預設值（`_INSECURE_SECRETS`）時，`AUTH_ENABLED=False`、`read_session` 一律回 None，不採信任何 cookie。
- **`email_verified`**：`verify_google_credential` 驗完簽章、aud、iss 之後，還要自己檢查 `email_verified`，沒驗證的信箱不得當站主。
- **`FORCE_OWNER` 在 PUBLIC 下被忽略**：`is_owner` 在公開站一律走登入比對，誤設環境變數也不會讓全站變站主。
- **file guard**（`auth.add_file_guard`）：對非站主擋 `exports/`、`projects/*/runs/*` 等私有檔；要同時涵蓋 `/gradio_api/file=` 和舊版相容路由 `/gradio_api/file/`（審查 SL-1）。不能改放 blocked_paths，否則站主下載 CSV／ZIP 也會被擋。
- **upload guard**（`auth.add_upload_guard`）：對非站主擋 POST `/gradio_api/upload`；移植時也要擋 POST `/gradio_api/process_recording`，它同樣會往暫存夾寫檔並觸發 ffmpeg（審查 SL-4）。ScopeLab 2026-11-07 已修：非站主 POST `upload`、`process_recording`、`component_server`、`vibe-*` 一律 403（`auth._OWNER_ONLY_POST`）；沒開登入的公開站走 `demo.launch`，同一張擋單由 `auth.launch_middleware` 的 `OwnerOnlyPostBlock` 全擋。
- **mount 模式下 Gradio 的 `_check_allowed` 不生效**：走 `mount_gradio_app` 時，不能靠 Gradio 幫忙擋 allowed_paths 以外的檔；預覽／圖庫要讀的路徑得在後端自己限制（審查 SL-3）。ScopeLab 2026-11-07 已修：訪客（`public_locked`）只看得到 card.json 有 `"public": true` 的專案（下拉清單、`on_proj_change`、bridge、訓練／推論分頁都在後端擋）；影像格／預覽用 `app._servable` 略過 allowed_paths 以外的檔；file guard 另外擋非公開專案的資料夾和它的來源原圖（`app._private_file`）。

## env.local.bat 鐵律（跨專案通用）

**ASCII + CRLF only**。放 `SCOPELAB_GOOGLE_CLIENT_ID` + `SCOPELAB_SESSION_SECRET`（`python -c "import secrets;print(secrets.token_urlsafe(48))"` 生一把）。LF 或含中文註解會害 cmd `call` 爆、連自啟都壞。`CLINSCOPE_FORCE_OWNER`/`SCOPELAB_FORCE_OWNER` **正式站絕不設**。

## 驗證清單

- `TestClient` /auth/google dummy → 401（不是 422）。
- 模擬 `identity_bar(owner)` 後 `public_locked(owner)=False`；再 `identity_bar(anon)` 後 `public_locked(owner)` 仍 False（競態不回鎖）、`public_locked(anon)=True`。
- 直接打事件：用 `tests/conftest.py` 的 `server.call(<qualname>, data, cookies=…)`（走 `/gradio_api/queue/join`，等同繞過 UI 的 API 呼叫），匿名與非站主登入都要被拒、站主照常（範例見 `tests/test_guard_compute.py`、`tests/test_guard_writes.py`）。新加的事件也要補一條。
- 瀏覽器：匿名→身分列有 Google 登入鈕 + 動作鈕虛化；站主登入→綠列 + checkbox 勾起 + 鈕可用 + 無限制 toast；取消勾選→鈕虛化 + 預覽 toast。
- 重啟見 [[dev-verify]]（含程序污染坑）。部署/tunnel 見 public-tunnel。
