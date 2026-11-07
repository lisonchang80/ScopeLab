"""沒開 Google 登入的公開站（走 demo.launch、沒有站主、沒有 mount 路徑的上傳／檔案閘門）。

SESSION_SECRET 太短（審查 GA-13）或沒設 client id 時，公開站會自動退到這條路徑：
- 上傳／錄影轉檔／component_server／vibe-* 的 POST 一律 403（審查 SL-4 的漏網路徑）
- 非公開專案的檔案經 file= 拿不到（blocked_paths，審查 SL-3）
- 一樣帶防框架標頭、跨站 POST 403
"""
import io
import json
import socket
import urllib.error
import urllib.request
from urllib.parse import quote

import pytest
from PIL import Image

import app
from conftest import DATA, IMAGES
from src import auth, store


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _req(url, method="GET", headers=None, data=None):
    req = urllib.request.Request(url, headers=headers or {}, method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.headers


def _file(base, path):
    return base + "/gradio_api/file=" + quote(str(path).replace("\\", "/"))


@pytest.fixture()
def noauth_server(monkeypatch):
    # 非公開專案：資料夾與來源都在 allowed（DATA）內
    src = DATA / "srcs" / "noauth_priv"
    src.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (199, 10, 10)).save(src / "privimg_0.png")
    pd = DATA / "projects" / "noauth_priv"
    pd.mkdir(parents=True, exist_ok=True)
    (pd / "card.json").write_text(json.dumps({"slug": "noauth_priv", "name": "私人", "task": "cls",
                                              "source_folder": str(src)}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(store, "PUBLIC", True)
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    built = []
    real_build = app.build
    monkeypatch.setattr(app, "build", lambda: built.append(real_build()) or built[-1])
    port = _free_port()
    app.launch_app(prevent_thread_lock=True, server_port=port, quiet=True)
    try:
        yield f"http://127.0.0.1:{port}", {"card": pd / "card.json", "source": src / "privimg_0.png"}
    finally:
        for demo in built:      # 只關自己起的那一個（gr.close_all 會連共用的 server fixture 一起關）
            demo.close()


def _mp4():
    boundary = "x7f3boundary"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"a.mp4\"\r\n"
            f"Content-Type: video/mp4\r\n\r\n").encode() + b"\x00\x00\x00\x18ftypmp42" + b"0" * 32 + \
        f"\r\n--{boundary}--\r\n".encode()
    return body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def test_owner_only_posts_blocked_without_login(noauth_server):
    base, _ = noauth_server
    body, headers = _mp4()
    for path in ("/gradio_api/process_recording", "/gradio_api/upload", "/gradio_api/component_server",
                 "/gradio_api/vibe-edit"):
        status, _h = _req(base + path, "POST", headers, body)
        assert status == 403, path


def test_private_project_files_blocked_without_login(noauth_server):
    base, files = noauth_server
    for f in files.values():
        assert _req(_file(base, f))[0] == 403, f
    # 公開範例照常
    assert _req(_file(base, IMAGES / "img_0.png"))[0] == 200
    assert _req(_file(base, DATA / "projects" / "demo" / "card.json"))[0] == 200


def test_frame_and_write_guards_without_login(noauth_server):
    base, _ = noauth_server
    status, h = _req(base + "/")
    assert status == 200 and h.get("x-frame-options") == "SAMEORIGIN"
    status, _h = _req(base + "/gradio_api/queue/join", "POST",
                      {"Sec-Fetch-Site": "same-site", "Content-Type": "application/json"}, b"{}")
    assert status == 403
