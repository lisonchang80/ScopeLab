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


def _get(url, host):
    req = urllib.request.Request(url, headers={"Host": host})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


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
    finally:
        for demo in built:      # 只關這個測試自己起的那一個（gr.close_all 會連共用的 server fixture 一起關掉）
            demo.close()
