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

import html as _html
import os
from datetime import datetime

import gradio as gr
from starlette.requests import Request   # 模組層匯入：因 `from __future__ import annotations`
                                         # 使註解變字串，FastAPI 需能在模組 globals 解析 Request

from . import store

OWNER_EMAIL = os.environ.get("SCOPELAB_OWNER_EMAIL", "lisonchang80@gmail.com").strip().lower()
GOOGLE_CLIENT_ID = os.environ.get("SCOPELAB_GOOGLE_CLIENT_ID", "").strip()
SESSION_SECRET = os.environ.get("SCOPELAB_SESSION_SECRET", "dev-insecure-change-me")
COOKIE_NAME = "sl_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30      # 30 天
# 不安全的 session 金鑰（repo 內公開的 dev 預設值 / 空值）：公開站用這些等於沒簽章。
_INSECURE_SECRETS = {"", "dev-insecure-change-me"}


def _secret_ok():
    """公開站必須設一把真正的 SESSION_SECRET，否則 cookie 可被偽造成站主。
    非 PUBLIC（桌面/開發）本機自用，預設金鑰無妨。"""
    return (not store.PUBLIC) or ((SESSION_SECRET or "").strip() not in _INSECURE_SECRETS)


# 站內 Google 登入是否啟用：公開站 + 有 client id + 有安全金鑰（fail-closed）。
AUTH_ENABLED = bool(store.PUBLIC and GOOGLE_CLIENT_ID and _secret_ok())
# Cloudflare Access 標頭信任開關。Access 已改 Bypass（匿名可進）後，cf-access-* 標頭
# 不再由邊緣可信注入——任何經 tunnel 的請求都能自帶 → 預設「不信任」，避免路人冒充站主。
# 僅在仍把 app 擺在 Access 強制登入後的過渡部署，才顯式設 SCOPELAB_TRUST_CF_ACCESS=1。
_TRUST_CF_ACCESS = os.environ.get("SCOPELAB_TRUST_CF_ACCESS", "").strip().lower() in ("1", "true", "yes")
_ID_LOG = store.ROOT / "identity_debug.log"    # 記真流量看到的 email（便於確認，可日後移除）

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


def add_auth_routes(fastapi_app):
    """掛在 Gradio 底層 FastAPI 上的登入/登出端點（站內 Google 登入）。"""
    from fastapi.responses import JSONResponse
    cookie_secure = os.environ.get("SCOPELAB_COOKIE_SECURE", "1") == "1"

    @fastapi_app.post("/auth/google")
    async def _auth_google(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            info = verify_google_credential((body or {}).get("credential", ""))
        except Exception as e:
            return JSONResponse({"error": f"invalid credential: {e}"}, status_code=401)
        email = (info.get("email") or "").strip().lower()
        resp = JSONResponse({"email": email, "owner": email == OWNER_EMAIL})
        resp.set_cookie(COOKIE_NAME, make_session(email), max_age=SESSION_MAX_AGE,
                        httponly=True, samesite="lax", secure=cookie_secure, path="/")
        return resp

    @fastapi_app.post("/auth/logout")
    async def _auth_logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE_NAME, path="/")
        return resp


def identity_bar(request: gr.Request):
    """載入時判定身分 → 回 (身分列HTML, 本地模式checkbox 更新)。公開站專用。
    站主一登入即自動開「本地模式」→ 限制立即解除（免再手動勾）；可再手動取消以預覽訪客。"""
    owner = is_owner(request)
    email = identity_email(request)
    # 站主載入 → 自動開本地模式（登入即解鎖）。非站主「不」動這個全域旗標：
    # 否則路人一載入就把旗標關成 False，會把已登入站主一起鎖掉（public_locked 對非站主本就恆擋）。
    if owner:
        set_local_mode(True)
    try:
        with open(_ID_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\temail={email}\towner={owner}\tauth={AUTH_ENABLED}\n")
    except Exception:
        pass
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
