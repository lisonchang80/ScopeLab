"""跨子網域 CSRF 與點擊劫持（審查 GA-3、GA-8）。

SameSite=Lax 的 cookie 對兄弟子網域照送，Gradio 內建 CORS 又對任何 Origin 回 ACAO＋credentials：
別的 *.tinghaochang.com 頁面只要有一處 XSS，就能以站主身分 POST /gradio_api/queue/join。
- 非 GET／HEAD／OPTIONS 一律要同源（Sec-Fetch-Site: same-origin／none；沒有 Fetch Metadata 就比 Origin 與 Host）。
- 非同源 Origin 的回應拿掉 ACAO／ACAC（預檢不過、GET 也讀不到）。
- 所有回應帶 X-Frame-Options／frame-ancestors 'self'。
- Gradio UI 自己的 POST（queue/join 等）是同源，照常。
"""
import uuid

import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from conftest import OWNER, owner_cookies
from src import auth

EVIL = "https://evil.tinghaochang.com"


def _join_body(server):
    return {"data": ["👁 圖示", None], "fn_index": server.fn_index("on_view"),
            "session_hash": uuid.uuid4().hex, "event_data": None, "trigger_id": None}


def _post_join(server, headers, cookies=None):
    with httpx.Client(base_url=server.base, cookies=cookies or {}, timeout=30) as c:
        return c.post("/gradio_api/queue/join", json=_join_body(server), headers=headers)


# ---- 同源寫入防線 -----------------------------------------------------------------
@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "none"},
    {},                                               # curl／腳本：沒有 Fetch Metadata 也沒有 Origin
    "same-origin-header",                             # 舊瀏覽器：沒有 Sec-Fetch-Site，Origin 與 Host 相同
], ids=["sec-fetch-same-origin", "sec-fetch-none", "no-headers", "origin-equals-host"])
def test_same_origin_post_allowed(server, headers):
    if headers == "same-origin-header":
        headers = {"Origin": server.base}
    r = _post_join(server, headers, cookies=owner_cookies())
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "same-site", "Origin": EVIL},
    {"Sec-Fetch-Site": "cross-site", "Origin": "https://attacker.example"},
    {"Sec-Fetch-Site": "same-site"},
    {"Origin": EVIL},                                 # 舊瀏覽器：Origin 與 Host 不同
    {"Sec-Fetch-Site": "same-site", "Origin": "same"},  # Fetch Metadata 優先：Origin 被偽造成同源也不算
], ids=["same-site", "cross-site", "same-site-no-origin", "foreign-origin", "same-site-spoofed-origin"])
def test_cross_site_post_rejected(server, headers):
    if headers.get("Origin") == "same":
        headers = {**headers, "Origin": server.base}
    r = _post_join(server, headers, cookies=owner_cookies())
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("path", ["/auth/logout", "/auth/google", "/gradio_api/upload"])
def test_cross_site_post_rejected_on_other_routes(server, path):
    with httpx.Client(base_url=server.base, cookies=owner_cookies(), timeout=30) as c:
        r = c.post(path, json={}, headers={"Sec-Fetch-Site": "same-site", "Origin": EVIL})
    assert r.status_code == 403, (path, r.text)


def test_get_not_affected_by_write_guard(server):
    with httpx.Client(base_url=server.base, timeout=30) as c:
        r = c.get("/", headers={"Sec-Fetch-Site": "cross-site"})   # 從別站點連結進來的導覽
    assert r.status_code == 200


# ---- 非同源 Origin 拿掉 ACAO／ACAC ------------------------------------------------
def _cors(resp):
    return {k: v for k, v in resp.headers.items() if k.lower().startswith("access-control-allow-")}


@pytest.mark.parametrize("path", ["/gradio_api/queue/join", "/gradio_api/upload", "/auth/google"])
def test_preflight_from_sibling_subdomain_gets_no_acao(server, path):
    with httpx.Client(base_url=server.base, timeout=30) as c:
        r = c.options(path, headers={"Origin": EVIL, "Access-Control-Request-Method": "POST",
                                     "Access-Control-Request-Headers": "content-type"})
    h = {k.lower() for k in _cors(r)}
    assert "access-control-allow-origin" not in h, (path, dict(r.headers))
    assert "access-control-allow-credentials" not in h, (path, dict(r.headers))


def test_get_from_sibling_subdomain_gets_no_acao(server):
    with httpx.Client(base_url=server.base, timeout=30) as c:
        r = c.get("/config", headers={"Origin": EVIL})
    h = {k.lower() for k in _cors(r)}
    assert "access-control-allow-origin" not in h and "access-control-allow-credentials" not in h


# ---- 防框架標頭 -------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/", "/config", "/gradio_api/info"])
def test_frame_headers_on_responses(server, path):
    with httpx.Client(base_url=server.base, timeout=30) as c:
        r = c.get(path)
    assert r.headers.get("x-frame-options") == "SAMEORIGIN", (path, dict(r.headers))
    assert "frame-ancestors 'self'" in (r.headers.get("content-security-policy") or ""), path


def test_frame_headers_on_403(server):
    r = _post_join(server, {"Sec-Fetch-Site": "same-site"})
    assert r.status_code == 403
    assert r.headers.get("x-frame-options") == "SAMEORIGIN"


def test_launch_middleware_order():
    """demo.launch 路徑：非公開模式 LocalOnly 一定在最外層；公開站沒開登入時不掛 LocalOnly、改掛 OwnerOnlyPostBlock。"""
    names = [m.cls.__name__ for m in auth.launch_middleware(local_only=True)]
    assert names == ["LocalOnlyMiddleware", "FrameGuard", "SameOriginWriteGuard"]
    names = [m.cls.__name__ for m in auth.launch_middleware(local_only=False)]
    assert names == ["FrameGuard", "SameOriginWriteGuard", "OwnerOnlyPostBlock"]   # 沒有站主：上傳等端點全擋


# ---- Secure 時 cookie 改名 __Host-sl_session ----------------------------------------
def test_cookie_name_prefix():
    assert auth.cookie_name(True) == "__Host-sl_session"
    assert auth.cookie_name(False) == "sl_session"
    assert auth.COOKIE_NAME == "sl_session"          # 測試跑在 http（SCOPELAB_COOKIE_SECURE=0）：維持原名


@pytest.fixture()
def secure_client(monkeypatch):
    monkeypatch.setattr(auth, "COOKIE_SECURE", True)
    monkeypatch.setattr(auth, "COOKIE_NAME", auth.cookie_name(True))
    monkeypatch.setattr(auth, "verify_google_credential",
                        lambda cred: {"email": OWNER, "email_verified": True})
    a = FastAPI()
    auth.add_auth_routes(a)
    return TestClient(a, base_url="https://testserver")


def _set_cookies(resp):
    return [v for k, v in resp.headers.multi_items() if k.lower() == "set-cookie"]


def test_secure_login_sets_host_prefixed_cookie(secure_client):
    r = secure_client.post("/auth/google", json={"credential": "x.y.z"})
    assert r.status_code == 200, r.text
    (sc,) = _set_cookies(r)
    assert sc.startswith("__Host-sl_session=")
    low = sc.lower()
    assert "secure" in low and "path=/" in low and "domain=" not in low and "httponly" in low
    token = sc.split(";", 1)[0].split("=", 1)[1]
    # 讀得回來：只認 __Host- 名稱；舊名 sl_session 的同一個值不再被採信
    assert auth.is_owner(_Req({"__Host-sl_session": token}))
    assert not auth.is_owner(_Req({"sl_session": token}))


def test_secure_logout_clears_both_names_with_secure(secure_client):
    r = secure_client.post("/auth/logout")
    assert r.status_code == 200
    scs = _set_cookies(r)
    names = sorted(sc.split("=", 1)[0] for sc in scs)
    assert names == ["__Host-sl_session", "sl_session"]
    assert all("secure" in sc.lower() for sc in scs)   # __Host- 的刪除不帶 Secure 會被瀏覽器拒收


class _Req:
    def __init__(self, cookies):
        self.cookies = cookies
        self.headers = {}
