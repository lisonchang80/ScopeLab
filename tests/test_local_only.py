"""非公開模式（桌面版／開發）只服務本機（審查 GA-1）。

這台機器的 cloudflared tunnel 常駐，scopelab／clinscope 子網域轉到 localhost:7861／7860。非公開實例人人是站主、
allowed_paths 是各磁碟根：只要落在被 tunnel 轉進來的埠，整顆磁碟就對網路公開。所以不看埠號，
帶 Cloudflare 標頭或 Host 不是本機的請求一律 403。
"""
import asyncio
import socket
import urllib.error
import urllib.request

import pytest

import app
from src import auth, store


def _h(**kw):
    return [(k.replace("_", "-").encode(), v.encode()) for k, v in kw.items()]


@pytest.mark.parametrize("host", ["127.0.0.1:7863", "localhost:7863", "localhost", "[::1]:7863", "LOCALHOST:7863"])
def test_local_hosts_pass(host):
    assert auth.local_request(_h(host=host))


@pytest.mark.parametrize("headers", [
    dict(host="scopelab.tinghaochang.com"),
    dict(host="clinscope.tinghaochang.com:443"),
    dict(host="127.0.0.1:7863", cf_connecting_ip="203.0.113.9"),
    dict(host="127.0.0.1:7863", cf_ray="8a1b2c3d4e5f-TPE"),
    dict(host="127.0.0.1:7863", cf_visitor='{"scheme":"https"}'),
    dict(host="127.0.0.1:7863", cdn_loop="cloudflare; loops=1"),
    dict(host="127.0.0.1.evil.example"),          # DNS rebinding
    dict(host="[::1].evil.example:80"),
    dict(),                                        # 沒有 Host
])
def test_tunnel_or_foreign_requests_rejected(headers):
    assert not auth.local_request(_h(**headers))


def _run(mw, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    asyncio.run(mw(scope, receive, send))
    return sent


def _inner():
    calls = []

    async def inner(scope, receive, send):
        calls.append(scope["type"])
    return inner, calls


def test_middleware_403_for_tunnel_http():
    inner, calls = _inner()
    sent = _run(auth.LocalOnlyMiddleware(inner), {"type": "http", "headers": _h(host="scopelab.tinghaochang.com")})
    assert sent[0]["status"] == 403 and not calls


def test_middleware_closes_tunnel_websocket():
    inner, calls = _inner()
    sent = _run(auth.LocalOnlyMiddleware(inner), {"type": "websocket", "headers": _h(host="scopelab.tinghaochang.com")})
    assert sent == [{"type": "websocket.close", "code": 1008}] and not calls


def test_middleware_passes_local_and_lifespan():
    inner, calls = _inner()
    mw = auth.LocalOnlyMiddleware(inner)
    assert _run(mw, {"type": "http", "headers": _h(host="127.0.0.1:7863")}) == []
    assert _run(mw, {"type": "lifespan"}) == []
    assert calls == ["http", "lifespan"]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url, host, method="GET", headers=None, full=False):
    req = urllib.request.Request(url, headers={"Host": host, **(headers or {})}, method=method,
                                 data=b"{}" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return (r.status, r.headers) if full else r.status
    except urllib.error.HTTPError as e:
        return (e.code, e.headers) if full else e.code


def test_desktop_mode_launches_and_only_serves_localhost(monkeypatch):
    """真的起一次非公開模式：以前 launch_app 把 delete_cache 傳給 Blocks.launch，直接 TypeError（桌面／開發起不來）；
    修好之後一定要有本機限定防線，否則一落在 tunnel 埠就公開整碟。"""
    monkeypatch.setattr(store, "PUBLIC", False)
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    built = []
    real_build = app.build
    monkeypatch.setattr(app, "build", lambda: built.append(real_build()) or built[-1])
    port = _free_port()
    app.launch_app(prevent_thread_lock=True, server_port=port, quiet=True)
    try:
        base = f"http://127.0.0.1:{port}"
        assert _get(base + "/", f"127.0.0.1:{port}") == 200
        assert _get(base + "/", "scopelab.tinghaochang.com") == 403
        win_ini = "/gradio_api/file=C:/Windows/win.ini"
        assert _get(base + win_ini, "scopelab.tinghaochang.com") == 403
        # 審查 GA-3／GA-8 的中介層跟 LocalOnly 共存：本機頁面帶防框架標頭；本機但跨站的 POST 被同源防線擋；
        # tunnel 進來的 POST 還是先被 LocalOnly 擋（回它的純文字 403，不是 JSON）。
        # 這幾個 POST 故意帶本文：中介層回 403 前要先讀掉本文，否則 Windows 用戶端常收到 WinError 10053（連線被中止）
        status, headers = _get(base + "/", f"127.0.0.1:{port}", full=True)
        assert status == 200 and headers.get("x-frame-options") == "SAMEORIGIN"
        join = base + "/gradio_api/queue/join"
        local = f"127.0.0.1:{port}"
        assert _get(join, local, "POST", {"Sec-Fetch-Site": "same-site", "Content-Type": "application/json"}) == 403
        assert _get(join, local, "POST", {"Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}) != 403
        status, headers = _get(join, "scopelab.tinghaochang.com", "POST",
                               {"Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}, full=True)
        assert status == 403 and headers.get("content-type", "").startswith("text/plain")
    finally:
        for demo in built:      # 只關這個測試自己起的那一個（gr.close_all 會連共用的 server fixture 一起關掉）
            demo.close()


def test_403_drains_request_body_first():
    """LocalOnly／SameOriginWriteGuard 回 403 前把（有上限的）本文讀完，連線才不會在用戶端還在送時被關掉。"""
    for mw, scope in ((auth.LocalOnlyMiddleware, {"type": "http", "headers": _h(host="scopelab.tinghaochang.com")}),
                      (auth.SameOriginWriteGuard, {"type": "http", "method": "POST",
                                                   "headers": _h(host="127.0.0.1", sec_fetch_site="same-site")})):
        chunks = [{"type": "http.request", "body": b"a" * 10, "more_body": True},
                  {"type": "http.request", "body": b"b" * 10, "more_body": False}]
        sent = []

        async def receive():
            return chunks.pop(0)

        async def send(msg):
            sent.append(msg)

        inner, calls = _inner()
        asyncio.run(mw(inner)(scope, receive, send))
        assert sent[0]["status"] == 403 and not calls
        assert chunks == [], mw.__name__          # 兩段本文都被讀掉了


@pytest.mark.parametrize("mw,scope", [
    (auth.LocalOnlyMiddleware, {"type": "http", "method": "POST", "path": "/x",
                                "headers": _h(host="scopelab.tinghaochang.com")}),
    (auth.SameOriginWriteGuard, {"type": "http", "method": "POST", "path": "/gradio_api/queue/join",
                                 "headers": _h(host="127.0.0.1", sec_fetch_site="same-site")}),
    (auth.OwnerOnlyPostBlock, {"type": "http", "method": "POST", "path": "/gradio_api/process_recording",
                               "headers": _h(host="127.0.0.1")}),
], ids=["LocalOnly", "SameOrigin", "OwnerOnlyPost"])
def test_403_not_stalled_by_slow_body(monkeypatch, mw, scope):
    """慢速送出：送一塊就停住、receive 永遠不回。drain 有時間上限，403 照樣準時送出（審查 GA-3 第二次退回）。"""
    import time
    assert auth._DRAIN_TIMEOUT <= 2
    monkeypatch.setattr(auth, "_DRAIN_TIMEOUT", 0.3)
    state = {"n": 0}

    async def receive():
        state["n"] += 1
        if state["n"] == 1:
            return {"type": "http.request", "body": b"a", "more_body": True}
        await asyncio.sleep(3600)

    sent = []

    async def send(msg):
        sent.append(msg)

    inner, calls = _inner()
    t0 = time.monotonic()
    asyncio.run(asyncio.wait_for(mw(inner)(scope, receive, send), 5))
    assert time.monotonic() - t0 < 2
    assert sent and sent[0]["status"] == 403 and not calls
