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
- **`app.py`**：`from src import ... auth`；build() 加 `identity_html`(elem_id `sl-identity`) + `local_chk`(elem_id `sl-local`, visible=False)；被擋 handler 改 `if auth.public_locked(request): gr.Warning(auth.locked_msg(request))`；`if store.PUBLIC:` 段掛 `demo.load(auth.identity_bar,…)` + `_gate_btns`(owner_button_gate) + `local_chk.change(_toggle_local,…)` + `AUTH_JS`；`launch_app` 在 `auth.AUTH_ENABLED` 時走 `mount_gradio_app(FastAPI)+uvicorn`（掛 `/auth/*`），否則原 `demo.launch`。CSS 加 `.sl-idbar/.sl-owner/.sl-guest/.sl-authbtn/.sl-gbtn/.sl-localchk` + `#sl-identity:empty{display:none}`。
- **`src/train/tab.py` / `src/infer/tab.py`**：被擋 handler 加 `request: gr.Request=None` + `if auth.public_locked(request)`；build 函式**多回傳動作鈕**供 app.py 虛化（train +train_btn；infer +pick/infer/single/save/csv）。
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
> 沒改 CF 成 Bypass 前，站仍在 Access 後；站主經 Access 的 `cf-access-authenticated-user-email` 標頭也能被 `_req_email` 認成 owner（過渡期可用），但匿名路人進不來。

## env.local.bat 鐵律（跨專案通用）

**ASCII + CRLF only**。放 `SCOPELAB_GOOGLE_CLIENT_ID` + `SCOPELAB_SESSION_SECRET`（`python -c "import secrets;print(secrets.token_urlsafe(48))"` 生一把）。LF 或含中文註解會害 cmd `call` 爆、連自啟都壞。`CLINSCOPE_FORCE_OWNER`/`SCOPELAB_FORCE_OWNER` **正式站絕不設**。

## 驗證清單

- `TestClient` /auth/google dummy → 401（不是 422）。
- 模擬 `identity_bar(owner)` 後 `public_locked(owner)=False`；再 `identity_bar(anon)` 後 `public_locked(owner)` 仍 False（競態不回鎖）、`public_locked(anon)=True`。
- 瀏覽器：匿名→身分列有 Google 登入鈕 + 動作鈕虛化；站主登入→綠列 + checkbox 勾起 + 鈕可用 + 無限制 toast；取消勾選→鈕虛化 + 預覽 toast。
- 重啟見 [[dev-verify]]（含程序污染坑）。部署/tunnel 見 public-tunnel。
