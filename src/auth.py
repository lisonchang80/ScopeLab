"""ScopeLab — 公開站身分／站主模式（移植自 ClinScope app.py 的登入層）。

公開站（SCOPELAB_PUBLIC=1）跑在站主本機上，經 Cloudflare tunnel 對外。
  - 匿名路人：可瀏覽範例專案、下載桌面版（唯讀）。
  - 站主（登入 email == OWNER_EMAIL）：勾「本地模式」後可用本機 GPU/資料夾
    （伺服器端原生選夾 + 訓練 + 單張重算 + 另存），等同桌面版全功能。

登入用「站內 Google Sign-In」：前端 GSI → ID token(JWT) → 後端驗簽/audience/issuer
→ 簽 itsdangerous cookie 記 email。需設 SCOPELAB_GOOGLE_CLIENT_ID 才啟用登入。

桌面版 / 開發（非 PUBLIC）：一律站主、無任何鎖（public_locked 恆 False）。
"""
from __future__ import annotations

import asyncio
import html as _html
import os
import re
from datetime import datetime

import gradio as gr
from starlette.requests import Request   # 模組層匯入：因 `from __future__ import annotations`
                                         # 使註解變字串，FastAPI 需能在模組 globals 解析 Request

from . import store

OWNER_EMAIL = os.environ.get("SCOPELAB_OWNER_EMAIL", "lisonchang80@gmail.com").strip().lower()
GOOGLE_CLIENT_ID = os.environ.get("SCOPELAB_GOOGLE_CLIENT_ID", "").strip()
SESSION_SECRET = os.environ.get("SCOPELAB_SESSION_SECRET", "dev-insecure-change-me")
# https 公開站（預設）cookie 帶 Secure；本機 http 開發設 SCOPELAB_COOKIE_SECURE=0。
COOKIE_SECURE = os.environ.get("SCOPELAB_COOKIE_SECURE", "1") == "1"
LEGACY_COOKIE_NAME = "sl_session"


def cookie_name(secure: bool) -> str:
    """Secure 時用 __Host- 前綴（審查 GA-3）：瀏覽器只收「Secure、Path=/、沒有 Domain」的同名 cookie，
    兄弟子網域（evil.tinghaochang.com）用 Domain=.tinghaochang.com 丟進來的 cookie 蓋不掉也冒充不了。
    本機 http 不能設 Secure，__Host- cookie 會被瀏覽器拒收 → 維持原名。"""
    return "__Host-sl_session" if secure else LEGACY_COOKIE_NAME


COOKIE_NAME = cookie_name(COOKIE_SECURE)
SESSION_MAX_AGE = 60 * 60 * 24 * 30      # 30 天
# 不安全的 session 金鑰（repo 內公開的 dev 預設值 / 空值）：公開站用這些等於沒簽章。
_INSECURE_SECRETS = {"", "dev-insecure-change-me"}
_MIN_SECRET_LEN = 32                     # 比照 ClinScope：太短的金鑰可暴力猜（審查 GA-13）


def _secret_ok():
    """公開站必須設一把真正的 SESSION_SECRET（≥32 字、不是 repo 內公開的預設值），否則 cookie 可被偽造成站主。
    不合格就 fail-closed：AUTH_ENABLED 關掉、read_session 一律不採信。
    非 PUBLIC（桌面/開發）本機自用，預設金鑰無妨。"""
    if not store.PUBLIC:
        return True
    s = (SESSION_SECRET or "").strip()
    return s not in _INSECURE_SECRETS and len(s) >= _MIN_SECRET_LEN


# 站內 Google 登入是否啟用：公開站 + 有 client id + 有安全金鑰（fail-closed）。
AUTH_ENABLED = bool(store.PUBLIC and GOOGLE_CLIENT_ID and _secret_ok())
# Cloudflare Access 標頭信任開關。Access 已改 Bypass（匿名可進）後，cf-access-* 標頭
# 不再由邊緣可信注入——任何經 tunnel 的請求都能自帶 → 預設「不信任」，避免路人冒充站主。
# 僅在仍把 app 擺在 Access 強制登入後的過渡部署，才顯式設 SCOPELAB_TRUST_CF_ACCESS=1。
_TRUST_CF_ACCESS = os.environ.get("SCOPELAB_TRUST_CF_ACCESS", "").strip().lower() in ("1", "true", "yes")
_ID_LOG = store.ROOT / "identity_debug.log"    # 記真流量看到的 email（便於確認，可日後移除）
_ID_LOG_MAX_BYTES = 1024 * 1024                # 1MB 上限：比照 ClinScope（審查 GA-6）

# 「本地模式」是站主專屬、程序層級的開關（公開站僅一位站主，故用全域旗標即可，
# 訪客永遠 is_owner=False 不受此旗標影響）。勾起才解鎖伺服器端本機操作。
_LOCAL_MODE = {"on": False}
_SER = None


def _serializer():
    global _SER
    if _SER is None:
        from itsdangerous import URLSafeTimedSerializer
        _SER = URLSafeTimedSerializer(SESSION_SECRET, salt="scopelab-session")
    return _SER


def make_session(email):
    return _serializer().dumps({"email": email})


def read_session(token):
    if not _secret_ok():        # 公開站沒設安全金鑰：cookie 可被偽造 → 一律不採信（fail-closed）
        return None
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
        return (data.get("email") or "").strip().lower() or None
    except Exception:
        return None


def verify_google_credential(credential):
    """驗 Google Sign-In 回傳的 ID token，回傳 claims（email/sub/…）。無 client id 則報錯。"""
    if not GOOGLE_CLIENT_ID:
        raise ValueError("SCOPELAB_GOOGLE_CLIENT_ID 未設定")
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token as gid
    info = gid.verify_oauth2_token(credential, g_requests.Request(), GOOGLE_CLIENT_ID,
                                   clock_skew_in_seconds=30)   # 容忍時鐘差，免「used too early」
    if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise ValueError("invalid issuer")
    # aud / exp 由 verify_oauth2_token 驗過；email_verified 需自己把關——
    # 未驗證的 email（Google 容許）不得據以登入成站主。只認布林 True 或字串 "true"：
    # 用真值判斷的話字串 "false"、"no"、1 都會過（審查 GA-11，比照 GIS／ClinScope／Portfolio）。
    verified = info.get("email_verified")
    if not (verified is True or verified == "true"):
        raise ValueError("email not verified")
    if not (info.get("email") or "").strip():
        raise ValueError("no email in token")
    return info


def _cookie_email(request):
    """從簽章 cookie 讀回登入 email；無/失效回 None。"""
    try:
        cookies = getattr(request, "cookies", None)
        if not cookies:
            raw = getattr(getattr(request, "request", None), "cookies", None)
            cookies = raw or {}
        if not cookies:                    # 後備：自己解析 Cookie 標頭
            h = getattr(request, "headers", None)
            ck = h.get("cookie") if (h and hasattr(h, "get")) else None
            cookies = {}
            if ck:
                for part in ck.split(";"):
                    if "=" in part:
                        k, v = part.strip().split("=", 1)
                        cookies[k] = v
        tok = cookies.get(COOKIE_NAME) if cookies else None
        return read_session(tok) if tok else None
    except Exception:
        return None


def _req_email(request):
    """讀 Cloudflare Access 認證後注入的登入 email 標頭（過渡/後備用）；無則 None。"""
    try:
        h = getattr(request, "headers", None)
        if h is None:
            return None
        v = None
        if hasattr(h, "get"):
            v = h.get("cf-access-authenticated-user-email") or h.get("Cf-Access-Authenticated-User-Email")
        if not v and hasattr(h, "items"):
            for k, vv in h.items():
                if str(k).lower() == "cf-access-authenticated-user-email":
                    v = vv
                    break
        return v.strip().lower() if v else None
    except Exception:
        return None


def identity_email(request):
    """目前登入 email：站內 Google 簽章 cookie 優先；僅在顯式開啟 _TRUST_CF_ACCESS 時
    才採信 CF Access 標頭（Bypass 下該標頭可被偽造，預設關閉）；皆無=匿名。"""
    email = _cookie_email(request)
    if email:
        return email
    if _TRUST_CF_ACCESS:
        return _req_email(request)
    return None


def is_owner(request):
    """公開站：僅登入 email == OWNER_EMAIL 才是站主（fail-closed —— 匿名/不符一律訪客）。
    桌面/dev（非 PUBLIC）一律站主。
    ⚠️ SCOPELAB_FORCE_OWNER 是本機/桌面的放行捷徑，在公開站（PUBLIC=1）一律忽略——
    否則誤設一個環境變數就讓全站任何人變站主（owner-mode skill 載明正式站絕不設）。"""
    if not store.PUBLIC:
        return True
    # 公開站忽略 SCOPELAB_FORCE_OWNER，一律走登入判定（owner email 比對）。
    return bool(OWNER_EMAIL) and identity_email(request) == OWNER_EMAIL


def set_local_mode(on):
    _LOCAL_MODE["on"] = bool(on)


def local_mode_on():
    return _LOCAL_MODE["on"]


def public_locked(request):
    """伺服器端本機操作（原生選夾/訓練/單張重算/另存）是否該擋？
    - 非 PUBLIC（桌面/開發）：永不擋。
    - PUBLIC 且非站主：擋（訪客唯讀）。
    - PUBLIC 且站主：登入即自動開「本地模式」→ 放行；站主可手動取消勾選以「預覽訪客唯讀」→ 暫時再擋。"""
    if not store.PUBLIC:
        return False
    if not is_owner(request):
        return True
    return not _LOCAL_MODE["on"]


def locked_msg(request):
    """被擋時要顯示的訊息：訪客 → 導去桌面版；站主（預覽中）→ 提示勾回本地模式。"""
    if is_owner(request):
        return "目前為訪客預覽（唯讀）。勾回上方「本地模式」即可用本機 GPU / 資料夾操作。"
    return store.PUBLIC_MSG


def guard_owner(request):
    """後端縱深防禦：非站主直接呼叫寫入/計算端點時擋下（UI 只是第一層）。"""
    if not is_owner(request):
        raise gr.Error("僅站主可執行此操作（您目前為訪客／唯讀）。")


def _same_origin(request):
    """CSRF 防線：POST 若帶了跨站 Origin（含其他 *.tinghaochang.com 子網域）就擋。
    app 自己的 fetch 是同源（Origin host == Host）；瀏覽器導覽有時不帶 Origin → 放行。"""
    try:
        origin = request.headers.get("origin")
        if not origin:
            return True
        from urllib.parse import urlsplit
        o = urlsplit(origin).netloc.lower()
        host = (request.headers.get("host") or "").lower()
        return bool(o) and o == host
    except Exception:
        return False


def add_auth_routes(fastapi_app):
    """掛在 Gradio 底層 FastAPI 上的登入/登出端點（站內 Google 登入）。"""
    from fastapi.responses import JSONResponse

    # COOKIE_NAME／COOKIE_SECURE 在請求當下讀模組變數（測試可 monkeypatch 成 Secure 驗 __Host- 前綴）。
    @fastapi_app.post("/auth/google")
    async def _auth_google(request: Request):
        if not _same_origin(request):
            return JSONResponse({"error": "cross-origin blocked"}, status_code=403)
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            info = verify_google_credential((body or {}).get("credential", ""))
        except Exception as e:
            # 例外內文（預期的 client id、時鐘差、「未設定」等）只記在伺服器端，不回給匿名呼叫者（審查 GA-10）
            print(f"[ScopeLab] rejected Google credential: {type(e).__name__}: {e}", flush=True)
            return JSONResponse({"error": "invalid credential"}, status_code=401)
        email = (info.get("email") or "").strip().lower()
        resp = JSONResponse({"email": email, "owner": email == OWNER_EMAIL})
        resp.set_cookie(COOKIE_NAME, make_session(email), max_age=SESSION_MAX_AGE,
                        httponly=True, samesite="lax", secure=COOKIE_SECURE, path="/")
        return resp

    @fastapi_app.post("/auth/logout")
    async def _auth_logout(request: Request):
        if not _same_origin(request):
            return JSONResponse({"error": "cross-origin blocked"}, status_code=403)
        resp = JSONResponse({"ok": True})
        # __Host- cookie 的刪除也要帶 Secure 才會被瀏覽器接受；改名前發的舊 sl_session 一併清掉。
        resp.delete_cookie(COOKIE_NAME, path="/", secure=COOKIE_SECURE, httponly=True, samesite="lax")
        if COOKIE_NAME != LEGACY_COOKIE_NAME:
            resp.delete_cookie(LEGACY_COOKIE_NAME, path="/", secure=COOKIE_SECURE, httponly=True, samesite="lax")
        return resp


# 非站主不得 POST 的 Gradio 端點（審查 SL-4）。ScopeLab 沒有上傳、錄影、server function、vibe 編輯器元件，
# UI 用不到這些；但端點照開：/upload 往暫存夾寫檔、/process_recording 寫檔再起 ffmpeg 重新編碼（不在佇列、
# 沒有並發上限）、/component_server 呼叫元件的 server function、vibe-* 是開發用編輯器。
# 只擋這幾個（不做全面白名單）：queue/join、cancel、reset、stream/* 等 UI 要用的照常。
_OWNER_ONLY_POST = re.compile(
    r"/gradio_api/(upload|process_recording|component_server|vibe-[^/]*|undo-vibe-[^/]*)/?$")


def owner_only_post(method: str, path: str) -> bool:
    """path 要給 scope["path"]（路由實際比對的已解碼字串），不要給 request.url.path（見 _scope_path）。
    結尾用 $ 而不是 \\Z：Starlette 的路由 regex 也用 $，同樣會吃掉結尾的換行，兩邊才一致。"""
    return method == "POST" and bool(_OWNER_ONLY_POST.search(path or ""))


def _scope_path(request) -> str:
    """路徑型閘門一律看 scope["path"]＝Gradio 路由實際比對的同一個已解碼字串（審查 SL-3 第二次退回）。
    不能用 request.url.path：Starlette 用已解碼的 path 重組網址再拆一次，路徑裡的 %3F／%23 解碼成 ?／# 之後，
    後面整段會被當成 query／fragment 切掉，閘門只看到前半段，Gradio 卻拿到完整路徑。"""
    return request.scope.get("path", "") or ""


def add_upload_guard(fastapi_app):
    """公開站：非站主不得 POST 到 Gradio 的上傳／錄影轉檔／component_server／vibe 端點（_OWNER_ONLY_POST）。
    ScopeLab 沒有任何上傳輸入元件（影像走伺服器端原生選夾），但 /gradio_api/upload 端點
    仍開著——匿名訪客可往伺服器暫存夾塞檔塞爆硬碟；/process_recording 還會起 ffmpeg 吃 CPU。站主（本地模式）照常。"""
    from starlette.responses import JSONResponse

    @fastapi_app.middleware("http")
    async def _guard_upload(request, call_next):
        if owner_only_post(request.method, _scope_path(request)) and public_locked(request):
            return JSONResponse({"error": "僅站主可使用這個端點（您目前為訪客／唯讀）。"}, status_code=403)
        return await call_next(request)


# Gradio 的檔案路由有兩個字首：/gradio_api/file=<路徑>，以及舊版相容的 /gradio_api/file/<路徑>
# （gradio routes.py 的 file_deprecated，最後走同一個 file_fetch）。只認 file= 的話舊路由整個繞過（審查 SL-1）。
_FILE_ROUTE = re.compile(r"/gradio_api/file(=|/)(.*)$", re.S)   # re.S：路徑中間夾換行也整段拿去判斷


def add_file_guard(fastapi_app, is_private):
    """公開站：非站主不得經 /gradio_api/file=（或舊路由 /gradio_api/file/）直接抓 is_private(路徑) 為真的檔
    （匯出結果、訓練權重、非公開專案的資料夾與來源原圖，見 app._private_file）。舊路由 /gradio_api/file/ 本站網址都不用
    （前端與下載鈕都是 file=），非站主一律擋，以後 Gradio 再改字首也不會漏。
    不放進 blocked_paths：站主按「下載 CSV／ZIP」時 Gradio 要先把 exports/ 的檔複製進快取，
    blocked 會連站主一起擋。公開範例專案的原圖照常服務。"""
    from urllib.parse import unquote
    from starlette.responses import JSONResponse

    @fastapi_app.middleware("http")
    async def _guard_file(request, call_next):
        m = _FILE_ROUTE.search(_scope_path(request))
        if m and public_locked(request):
            if m.group(1) == "/":
                return JSONResponse({"error": "請改用 /gradio_api/file= 網址。"}, status_code=403)
            raw = m.group(2)
            # 原樣、再解碼一次、去掉結尾換行都要查：防編碼或換行讓這裡看到的路徑跟 Gradio 實際讀的不一樣
            cands = {raw, unquote(raw), raw.rstrip("\n"), unquote(raw).rstrip("\n")}
            if any(is_private(c) for c in cands):
                return JSONResponse({"error": "僅站主可下載這個檔案。"}, status_code=403)
        return await call_next(request)


# ── 非公開模式（桌面版／開發）只服務本機（審查 GA-1） ─────────────────────
# 非 PUBLIC 時人人是站主、allowed_paths 是各磁碟根。這台機器的 cloudflared tunnel 常駐，把 scopelab／clinscope
# 子網域轉到 localhost:7861／7860：非公開實例只要落在這兩個埠（或任何被 tunnel 轉進來的埠），整顆磁碟與站主權限
# 就對網路公開。不管埠號，經 tunnel 進來的請求都帶 Cloudflare 標頭、Host 是公開網域 → 一律 403；DNS rebinding 也一併擋掉。
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
_CF_HEADERS = {b"cf-connecting-ip", b"cf-ray", b"cf-visitor"}
LOCAL_ONLY_MSG = "這個程式只在本機開放（非公開模式）；公開站請用 SCOPELAB_PUBLIC=1 啟動。"


def _host_only(host: str) -> str:
    """Host 標頭去掉埠號：127.0.0.1:7863 → 127.0.0.1、[::1]:7863 → [::1]。"""
    host = (host or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        rest = host[end + 1:] if end > 0 else "x"
        # 括號後面只能是結尾或「:埠號」；[::1].evil.example 之類原樣回傳（不算本機）
        return host[: end + 1] if rest == "" or (rest.startswith(":") and rest[1:].isdigit()) else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def local_request(headers) -> bool:
    """headers：ASGI scope 的 [(name_bytes, value_bytes), ...]。帶 Cloudflare 標頭或 Host 不是本機就不是本機請求。"""
    host = ""
    for name, value in headers:
        name = name.lower()
        if name in _CF_HEADERS:
            return False
        if name == b"cdn-loop" and b"cloudflare" in value.lower():
            return False
        if name == b"host":
            host = value.decode("latin-1")
    return _host_only(host) in _LOCAL_HOSTS


_DRAIN_LIMIT = 64 * 1024
_DRAIN_TIMEOUT = 1.0          # 秒：慢速送出（送一塊就停住）也不能把 403 卡住（驗收要求 2 秒內回 403，留一倍餘裕）


async def _drain_body(receive, limit=_DRAIN_LIMIT, timeout=None):
    """回 403 前讀掉（有上限的）請求本文。沒讀就回應並關連線的話，用戶端還在送本文時會收到
    連線被中止（Windows 的 WinError 10053）而不是 403。超過上限或超過 timeout（預設 _DRAIN_TIMEOUT）就不再讀，
    直接回應（審查 GA-3 第二次退回：慢速送出時三個中介層都卡住）。"""
    async def _read():
        seen = 0
        while seen <= limit:
            msg = await receive()
            if msg.get("type") != "http.request":
                return
            seen += len(msg.get("body", b""))
            if not msg.get("more_body"):
                return
    try:
        await asyncio.wait_for(_read(), _DRAIN_TIMEOUT if timeout is None else timeout)
    except asyncio.TimeoutError:
        pass


async def _send_403(send, body: bytes, content_type: bytes):
    await send({"type": "http.response.start", "status": 403,
                "headers": [(b"content-type", content_type),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class LocalOnlyMiddleware:
    """純 ASGI 中介層：非本機的 http 請求回 403、websocket 直接關；lifespan 照常。只掛在非 PUBLIC 模式。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and not local_request(scope.get("headers") or []):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await _drain_body(receive)
            await _send_403(send, LOCAL_ONLY_MSG.encode("utf-8"), b"text/plain; charset=utf-8")
            return
        await self.app(scope, receive, send)


# ── 跨子網域 CSRF／點擊劫持（審查 GA-3、GA-8；邏輯照抄 GIS server/security.py 與 Portfolio server/middleware.py） ──
# SameSite=Lax 的 cookie 對兄弟子網域（evil.tinghaochang.com → scopelab.tinghaochang.com 算 same-site）照送，
# 而 Gradio 內建 CORS 在 Host 不是 localhost 時對任何 Origin 都回 ACAO＋credentials：別的子網域只要有一處 XSS，
# 就能以站主身分 POST /gradio_api/queue/join 觸發訓練、刪專案，再用 queue/data 讀回結果。
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _hdr(scope, name: bytes):
    for k, v in scope.get("headers") or []:
        if k.lower() == name:
            return v.decode("latin-1")
    return None


def _origin_matches_host(origin: str, host: str) -> bool:
    from urllib.parse import urlsplit
    try:
        o = urlsplit(origin).netloc.lower()
    except ValueError:
        return False
    return bool(o) and o == (host or "").strip().lower()


def is_same_origin_request(scope) -> bool:
    """照 GIS is_same_origin_request：有 Sec-Fetch-Site 就只認 same-origin／none；
    舊瀏覽器沒有 Fetch Metadata 就比對 Origin 與 Host；兩個都沒有（curl、腳本）放行——它們不會自動帶受害者的 cookie。"""
    site = _hdr(scope, b"sec-fetch-site")
    if site is not None:
        return site.strip().lower() in ("same-origin", "none")
    origin = _hdr(scope, b"origin")
    if origin is None:
        return True
    return _origin_matches_host(origin, _hdr(scope, b"host") or "")


class SameOriginWriteGuard:
    """所有非 GET／HEAD／OPTIONS 的 http 請求（含 /gradio_api/queue/join、/upload、/auth/*）都要同源，否則 403。
    Gradio 前端自己的 fetch 是同源，照常。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] == "http" and scope.get("method", "GET").upper() not in _SAFE_METHODS
                and not is_same_origin_request(scope)):
            await _drain_body(receive)
            await _send_403(send, b'{"error":"cross-site request blocked"}', b"application/json")
            return
        await self.app(scope, receive, send)


_CORS_STRIP = {b"access-control-allow-origin", b"access-control-allow-credentials"}


class StripCrossOriginCors:
    """Origin 跟 Host 不同（別的網站、含兄弟子網域）就拿掉 Gradio 加的 ACAO／ACAC：預檢不過、
    GET（queue/data、/config）也讀不到回應。同源請求本來就不需要 CORS 標頭。要包在 Gradio 外層才拿得到。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        origin = _hdr(scope, b"origin")
        if origin is None or _origin_matches_host(origin, _hdr(scope, b"host") or ""):
            await self.app(scope, receive, send)
            return

        async def send_stripped(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [(k, v) for k, v in message.get("headers", [])
                                                  if k.lower() not in _CORS_STRIP]}
            await send(message)

        await self.app(scope, receive, send_stripped)


class FrameGuard:
    """防點擊劫持：同站子網域的 iframe 會帶站主 cookie（SameSite=Lax 不擋同站），嵌入本站就能誘騙站主點
    「刪除專案→確定」「開始訓練」，而且請求是本站文件自己發的、同源檢查擋不到。只允許同源嵌入。"""

    HEADERS = [
        (b"x-frame-options", b"SAMEORIGIN"),
        (b"content-security-policy", b"frame-ancestors 'self'"),
    ]

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *self.HEADERS]}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def add_web_guards(fastapi_app):
    """站內登入路徑（外層 FastAPI 包住掛在 "/" 的 Gradio）：由外到內 FrameGuard → StripCrossOriginCors →
    SameOriginWriteGuard → 既有的上傳／檔案閘門 → Gradio（含它自己的 CORS）。
    add_middleware 後加的在外層，所以要在 add_upload_guard／add_file_guard 之後呼叫。"""
    fastapi_app.add_middleware(SameOriginWriteGuard)
    fastapi_app.add_middleware(StripCrossOriginCors)
    fastapi_app.add_middleware(FrameGuard)


class OwnerOnlyPostBlock:
    """沒開登入的公開站（demo.launch、沒有站主）：_OWNER_ONLY_POST 的端點（upload、process_recording、
    component_server、vibe-*）一律 403（審查 SL-4 的漏網路徑：金鑰太短或沒設 client id 時會自動退到這條路徑）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and owner_only_post(scope.get("method", "GET").upper(), scope.get("path", "")):
            await _drain_body(receive)
            await _send_403(send, b'{"error":"public demo: endpoint disabled"}', b"application/json")
            return
        await self.app(scope, receive, send)


def launch_middleware(local_only: bool):
    """demo.launch 路徑（桌面／開發、或公開站沒開登入）用的 app_kwargs middleware，第一個在最外層。
    非公開模式 LocalOnlyMiddleware 一定最先跑（非本機請求不論方法一律 403）；
    沒開登入的公開站（local_only=False）沒有站主 → 上傳／錄影轉檔等端點直接全擋（OwnerOnlyPostBlock）。
    註：Gradio 的 CORS／Brotli 是之後 add_middleware 加的，會包在這些外面；那兩種模式不是本機就是沒有站主，
    不靠拿掉 ACAO（非公開模式 Host 必是本機，Gradio 的 strict CORS 只回本機 Origin）。"""
    from starlette.middleware import Middleware
    mws = [Middleware(LocalOnlyMiddleware)] if local_only else []
    mws += [Middleware(FrameGuard), Middleware(SameOriginWriteGuard)]
    if not local_only:
        mws.append(Middleware(OwnerOnlyPostBlock))
    return mws


def _log_identity(email, owner):
    """只記登入者（審查 GA-6，比照 ClinScope）：匿名載入不寫檔——否則任何人狂打 identity_bar 對應的事件
    就能無限長檔吃滿 D:；檔案超過 _ID_LOG_MAX_BYTES 就不再追加。"""
    if not email:
        return
    try:
        if os.path.exists(_ID_LOG) and os.path.getsize(_ID_LOG) >= _ID_LOG_MAX_BYTES:
            return
        with open(_ID_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\temail={email}\towner={owner}\tauth={AUTH_ENABLED}\n")
    except Exception:
        pass


def identity_bar(request: gr.Request):
    """載入時判定身分 → 回 (身分列HTML, 本地模式checkbox 更新)。公開站專用。
    站主一登入即自動開「本地模式」→ 限制立即解除（免再手動勾）；可再手動取消以預覽訪客。"""
    owner = is_owner(request)
    email = identity_email(request)
    # 站主載入 → 自動開本地模式（登入即解鎖）。非站主「不」動這個全域旗標：
    # 否則路人一載入就把旗標關成 False，會把已登入站主一起鎖掉（public_locked 對非站主本就恆擋）。
    if owner:
        set_local_mode(True)
    _log_identity(email, owner)
    logout_btn = '<button class="sl-authbtn" id="sl-logout">登出</button>' if AUTH_ENABLED else ''
    if owner:
        bar = ('<div class="sl-idbar sl-owner">✅ 已登入站主 <b>' + _html.escape(email or OWNER_EMAIL)
               + '</b> · 可完整操作（取消「本地模式」可預覽訪客唯讀）' + logout_btn + '</div>')
    elif email:      # 已登入但非站主
        bar = ('<div class="sl-idbar sl-guest">👁 訪客（唯讀）· 目前登入 <b>' + _html.escape(email)
               + '</b>，非站主' + logout_btn + '</div>')
    else:            # 匿名
        login = '<span class="sl-gbtn" id="sl-gbtn"></span>' if AUTH_ENABLED else ''
        tail = ('　站主登入 ' + login) if AUTH_ENABLED else ''
        bar = ('<div class="sl-idbar sl-guest">👁 訪客模式（唯讀）· 選資料夾 / 訓練請下載桌面版'
               + tail + '</div>')
    return bar, gr.update(visible=owner, value=owner)


def owner_button_gate(n, request: gr.Request):
    """回 n 個按鈕的可用性更新：站主→可用；訪客→虛化停用（唯讀）。
    只在公開站載入時呼叫；桌面/開發不呼叫故按鈕維持預設可用。"""
    en = gr.update(interactive=is_owner(request))
    return [en] * n


# 站內 Google 登入前端：載入 GSI SDK → 匿名時把官方 Google 按鈕 render 進身分列的 #sl-gbtn；
# 拿到 credential(ID token) POST /auth/google 成功即 reload（讓 identity_bar 依 cookie 重判）；
# 「登出」鈕 POST /auth/logout 後 reload。僅 AUTH_ENABLED（有 client id）時掛。
AUTH_JS = ("""
() => {
  if (window.__sl_auth_init) return;
  window.__sl_auth_init = true;
  const CID = "__GOOGLE_CLIENT_ID__";
  const post = (url) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' } });
  document.addEventListener('click', (e) => {
    if (e.target && e.target.id === 'sl-logout') {
      e.preventDefault();
      post('/auth/logout').finally(() => location.reload());
    }
  }, true);
  const onCred = (resp) => {
    if (!resp || !resp.credential) return;
    fetch('/auth/google', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ credential: resp.credential })
    }).then(r => r.json().then(j => ({ ok: r.ok, j }))).then(({ ok, j }) => {
      if (ok) location.reload();
      else alert('登入失敗：' + ((j && j.error) || '請確認使用站主 Google 帳號'));
    }).catch(() => alert('登入失敗（網路問題），請再試一次。'));
  };
  let inited = false;
  const initGsi = () => {
    if (inited || !(window.google && google.accounts && google.accounts.id)) return;
    inited = true;
    google.accounts.id.initialize({ client_id: CID, callback: onCred, auto_select: false });
  };
  let rendered = null;
  const tryRender = () => {
    initGsi();
    const el = document.getElementById('sl-gbtn');
    if (el && inited && el !== rendered && !el.childElementCount) {
      try { google.accounts.id.renderButton(el, { type: 'standard', theme: 'outline', size: 'medium', text: 'signin', shape: 'pill' }); rendered = el; } catch (_) {}
    }
  };
  const s = document.createElement('script');
  s.src = 'https://accounts.google.com/gsi/client'; s.async = true; s.defer = true;
  s.onload = () => { initGsi(); tryRender(); };
  document.head.appendChild(s);
  setInterval(tryRender, 700);
}
""").replace("__GOOGLE_CLIENT_ID__", GOOGLE_CLIENT_ID)
