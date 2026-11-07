"""請求本文上限（審查 GA-12）：/auth/* 64KB、queue/join 等其他端點 8MB（站主 100MB）、上傳端點交給 max_file_size。

以前外層 FastAPI 沒有任何上限：匿名對 /auth/google 送 8MB JSON，伺服器整包讀入並解析、4 秒多後才回 401。
沒有 Content-Length 的 chunked 請求也要擋（邊收邊數）。
"""
import hashlib
import json
import os
import tempfile
import time

import pytest

from conftest import owner_cookies
from src import auth

MB = 1024 * 1024


def _join_body(server, pad_bytes, fn="on_view"):
    fid = server.fn_index(fn)
    return json.dumps({"data": ["👁 詳細資料", None], "fn_index": fid, "session_hash": "bodylimit",
                       "event_data": None, "trigger_id": None, "pad": "x" * pad_bytes}).encode()


def _chunks(total, size=16 * 1024):
    sent = 0
    while sent < total:
        n = min(size, total - sent)
        sent += n
        yield b" " * n


# ---- 上限數字與量測（量測方法同 src/auth.py 註解） --------------------------------
def _paths_payload(names):
    tmp = os.path.join(tempfile.gettempdir(), "gradio")
    paths = [os.path.join(tmp, hashlib.sha256(str(i).encode()).hexdigest(), n) for i, n in enumerate(names)]
    inner = json.dumps(paths, ensure_ascii=False)              # 前端 JSON.stringify(paths)
    return len(json.dumps({"data": [inner, None], "event_data": None, "fn_index": 123, "trigger_id": 456,
                           "session_hash": "abcdefghij1"}, ensure_ascii=False).encode("utf-8"))


def test_queue_limit_at_least_twice_measured_5000_paths():
    n = 5000
    worst = max(_paths_payload([f"IMG_20240315_143022_{i:04d}.jpg" for i in range(n)]),
                _paths_payload([("顯" * 120) + f"{i:04d}.png" for i in range(n)]))
    assert worst < 2.6 * MB
    assert auth.QUEUE_BODY_LIMIT >= 2 * worst
    assert auth.QUEUE_BODY_LIMIT == 8 * MB


def test_limits_by_path():
    assert auth.body_limit("/auth/google") == 64 * 1024
    assert auth.body_limit("/auth/logout") == 64 * 1024
    assert auth.body_limit("/gradio_api/queue/join") == 8 * MB
    assert auth.body_limit("/gradio_api/queue/join", owner=True) == 100 * MB
    assert auth.body_limit("/gradio_api/upload") is None
    assert auth.body_limit("/gradio_api/process_recording") is None
    assert auth.body_limit("/gradio_api/upload/../queue/join") == 8 * MB      # 只豁免完整路徑


# ---- 經伺服器（與正式站同一條 launch_app 路徑） --------------------------------------
def test_auth_google_large_body_413_fast(server):
    t = time.monotonic()
    with server.client() as c:
        r = c.post("/auth/google", content=b'{"credential":"' + b"a" * (8 * MB) + b'"}',
                   headers={"Content-Type": "application/json"})
    assert r.status_code == 413, r.text[:200]
    assert time.monotonic() - t < 4


def test_auth_google_chunked_over_limit_413(server):
    with server.client() as c:
        r = c.post("/auth/google", content=_chunks(200 * 1024), headers={"Content-Type": "application/json"})
    assert r.request.headers.get("transfer-encoding") == "chunked"
    assert r.status_code == 413, r.text[:200]


def test_auth_google_small_chunked_still_works(server):
    body = json.dumps({"credential": "x.y"}).encode()
    with server.client() as c:
        r = c.post("/auth/google", content=iter([body[:5], body[5:]]), headers={"Content-Type": "application/json"})
    assert r.request.headers.get("transfer-encoding") == "chunked"
    assert r.status_code == 401, r.text[:200]                # 照常走到驗證（不是 413、也不是 422）


def test_queue_join_anonymous_over_8mb_413(server):
    with server.client() as c:
        r = c.post("/gradio_api/queue/join", content=_join_body(server, 9 * MB),
                   headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_queue_join_anonymous_chunked_over_8mb_413(server):
    with server.client() as c:
        r = c.post("/gradio_api/queue/join", content=_chunks(9 * MB, 256 * 1024),
                   headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_queue_join_normal_size_ok(server):
    with server.client() as c:
        r = c.post("/gradio_api/queue/join", content=_join_body(server, 1 * MB),
                   headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text[:200]


def test_queue_join_chunked_under_limit_ok(server):
    body = _join_body(server, 512 * 1024)
    with server.client() as c:
        r = c.post("/gradio_api/queue/join", content=iter([body[i:i + 65536] for i in range(0, len(body), 65536)]),
                   headers={"Content-Type": "application/json"})
    assert r.request.headers.get("transfer-encoding") == "chunked"
    assert r.status_code == 200, r.text[:200]


def test_owner_editor_save_sized_body_ok(server):
    """站主存回一張 5MP 編輯影像約 11.5MB（PNG dataURL）：不能被 8MB 擋掉。"""
    with server.client(cookies=owner_cookies()) as c:
        r = c.post("/gradio_api/queue/join", content=_join_body(server, 12 * MB),
                   headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text[:200]


def test_upload_exempt_for_owner(server):
    """上傳端點不受 8MB 限制（交給 max_file_size，公開站 25MB）；非站主照樣被上傳閘門 403。"""
    files = {"files": ("a.bin", b"\0" * (9 * MB), "application/octet-stream")}
    with server.client(cookies=owner_cookies()) as c:
        r = c.post("/gradio_api/upload", files=files)
    assert r.status_code == 200, r.text[:200]
    with server.client() as c:
        r = c.post("/gradio_api/upload", files={"files": ("a.bin", b"\0" * 1024, "application/octet-stream")})
    assert r.status_code == 403


def test_launch_middleware_paths():
    """沒開登入的公開站（demo.launch 路徑）也掛上限、且不分站主；桌面／開發只服務本機、不設上限。"""
    pub = [(m.cls, m.kwargs) for m in auth.launch_middleware(local_only=False)]
    assert (auth.BodySizeLimit, {"owner_aware": False}) in pub
    assert all(c is not auth.BodySizeLimit for c, _ in
               [(m.cls, m.kwargs) for m in auth.launch_middleware(local_only=True)])
