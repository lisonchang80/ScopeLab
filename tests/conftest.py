"""ScopeLab 測試共用設定。

- 一律以「公開站」模式跑（SCOPELAB_PUBLIC=1），站主信箱用合成值 owner@example.com。
- 資料根導到暫存目錄（SCOPELAB_DATA_DIR），內含一個合成的分類專案；不碰 repo 的 projects/。
- 不訓練、不推論、不連網：torch / Google 驗簽都在個別測試裡 stub 掉。
- `server` fixture 走與正式站相同的 `app.launch_app()`（uvicorn + /auth/* 路由），
  `call()` 直接打 /gradio_api/queue/join（按 fn_index，等同繞過 UI 虛化的 API 呼叫）。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="scopelab-test-"))
DATA = TMP / "data"
IMAGES = TMP / "images"
OWNER = "owner@example.com"
CLIENT_ID = "test-client.apps.googleusercontent.com"
SLUG = "demo"

os.environ.update({
    "SCOPELAB_PUBLIC": "1",
    "SCOPELAB_OWNER_EMAIL": OWNER,
    "SCOPELAB_GOOGLE_CLIENT_ID": CLIENT_ID,
    "SCOPELAB_SESSION_SECRET": "test-only-session-secret",
    "SCOPELAB_DATA_DIR": str(DATA),
    "SCOPELAB_COOKIE_SECURE": "0",
    # 比照正式站 autostart：允許整個 repo 根 + 合成影像夾
    "SCOPELAB_ALLOWED_PATHS": f"{ROOT};{IMAGES}",
    "GRADIO_TEMP_DIR": str(TMP / "gradio"),
    "GRADIO_ANALYTICS_ENABLED": "False",
})
for k in ("SCOPELAB_FORCE_OWNER", "SCOPELAB_TRUST_CF_ACCESS"):
    os.environ.pop(k, None)


def _make_images():
    from PIL import Image
    IMAGES.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        Image.new("RGB", (32, 32), (40 * i, 80, 160)).save(IMAGES / f"img_{i}.png")


CARD = {"slug": SLUG, "name": "合成示範", "discipline": "測試", "task": "cls",
        "classes": ["甲", "乙"], "author": "", "version": "0.1.0", "license": "CC0",
        "description": "", "source_folder": str(IMAGES),
        "created": "2099-01-01", "updated": "2099-01-01"}


def reset_project():
    """把合成專案還原成初始狀態（每個測試前呼叫），並清掉 exports/ 與其他專案。"""
    import shutil
    projects = DATA / "projects"
    if projects.exists():
        shutil.rmtree(projects)
    shutil.rmtree(DATA / "exports", ignore_errors=True)
    pd = projects / SLUG
    pd.mkdir(parents=True)
    (pd / "card.json").write_text(json.dumps(CARD, ensure_ascii=False), encoding="utf-8")
    (pd / "labels.json").write_text(json.dumps({"labels": {}, "edits": {}}), encoding="utf-8")


_make_images()
reset_project()

sys.path.insert(0, str(ROOT))
import app  # noqa: E402
from src import auth, store  # noqa: E402

auth._ID_LOG = TMP / "identity_debug.log"     # 別寫進 repo 根


class FakeRequest:
    """足以讓 auth.is_owner / public_locked 判定的假 request（直接呼叫 handler 用）。"""

    def __init__(self, cookies=None, headers=None):
        self.cookies = dict(cookies or {})
        self.headers = dict(headers or {})


def owner_cookies():
    return {auth.COOKIE_NAME: auth.make_session(OWNER)}


def guest_cookies():
    return {auth.COOKIE_NAME: auth.make_session("visitor@example.org")}


def read_card():
    return json.loads((DATA / "projects" / SLUG / "card.json").read_text(encoding="utf-8"))


def read_labels():
    return json.loads((DATA / "projects" / SLUG / "labels.json").read_text(encoding="utf-8"))


class Server:
    def __init__(self, base, blocks):
        self.base, self.blocks = base, blocks

    def fn_index(self, qualname):
        hits = [i for i, bf in self.blocks.fns.items()
                if bf.fn is not None and getattr(bf.fn, "__qualname__", "") == qualname]
        assert len(hits) == 1, f"{qualname}: {hits}"
        return hits[0]

    def client(self, cookies=None):
        import httpx
        return httpx.Client(base_url=self.base, cookies=cookies or {}, timeout=30)

    def call(self, qualname, data, cookies=None, session=None):
        """經 queue/join 觸發事件，回 (success, output|error)。session 相同 = 同一個瀏覽器分頁。"""
        session = session or uuid.uuid4().hex
        fid = self.fn_index(qualname)
        with self.client(cookies) as c:
            r = c.post("/gradio_api/queue/join", json={
                "data": data, "fn_index": fid, "session_hash": session,
                "event_data": None, "trigger_id": None})
            assert r.status_code == 200, r.text
            with c.stream("GET", f"/gradio_api/queue/data?session_hash={session}") as resp:
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    m = json.loads(line[5:])
                    if m.get("msg") == "process_completed":
                        return bool(m.get("success")), m.get("output") or {}
        raise AssertionError("queue 沒有回 process_completed")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def server():
    import httpx
    from starlette.routing import Mount
    port = _free_port()
    mounted = app.launch_app(prevent_thread_lock=True, server_port=port)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/", timeout=2).status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    gapp = [r for r in mounted.routes if isinstance(r, Mount)][0].app
    return Server(base, gapp.get_blocks())


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    """每個測試：還原合成專案、預設開本地模式（站主登入後的狀態）。"""
    reset_project()
    auth.set_local_mode(True)
    yield
    auth.set_local_mode(True)
