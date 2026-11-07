"""Gradio /gradio_api/file= 的曝險範圍，與匿名上傳防護。"""
import io

import pytest

import app
from conftest import OWNER, ROOT, owner_cookies
from src import store


def _file_url(abs_path, prefix="/gradio_api/file="):
    from urllib.parse import quote
    from pathlib import Path
    return prefix + quote(Path(abs_path).resolve().as_posix())


# Gradio 有兩個檔案路由字首：file= 與舊版相容的 file/（同一個 file_fetch）。只測 file= 會漏掉繞道（審查 SL-1）
PREFIXES = ["/gradio_api/file=", "/gradio_api/file/"]


# ---- 敏感檔不得被服務（blocked_paths 優先） ------------------------------------
@pytest.mark.parametrize("rel", [".git/config", ".git/HEAD"])
def test_git_dir_blocked(server, rel):
    with server.client() as c:
        r = c.get(_file_url(ROOT / rel))
    assert r.status_code == 403, rel


def test_env_local_and_idlog_blocked(server):
    # 這些檔在正式站存在且含祕密：落在 blocked 內。不在 repo 裡建假檔（測試絕不寫真的 repo，審查 SL-2）；
    # 這台機器上真的有 env.local.bat 時，順便確認伺服器真的回 403。
    envf = ROOT / "packaging" / "autostart" / "env.local.bat"
    _, blocked = app._file_access_paths()
    assert str(envf) in blocked
    assert any(b.endswith("identity_debug.log") for b in blocked)
    if envf.exists():
        with server.client() as c:
            for prefix in PREFIXES:
                assert c.get(_file_url(envf, prefix)).status_code == 403, prefix


# ---- 正常檔仍可服務（沒有過度封鎖） --------------------------------------------
def test_sample_image_served(server):
    img = next((ROOT / "samples" / "shapes").glob("*.png"))
    with server.client() as c:
        r = c.get(_file_url(img))
    assert r.status_code == 200


# ---- 匯出結果與訓練權重只給站主（路徑猜得到也不行） -------------------------------
def _private_files():
    exp = store.DATA_ROOT / "exports" / "infer_results.csv"
    exp.parent.mkdir(parents=True, exist_ok=True)
    exp.write_text("file,class\n", encoding="utf-8")
    pt = store.PROJECTS / "demo" / "runs" / "20990101-000000" / "best.pt"
    pt.parent.mkdir(parents=True, exist_ok=True)
    pt.write_bytes(b"weights")
    return exp, pt


@pytest.mark.parametrize("prefix", PREFIXES)
def test_exports_and_weights_blocked_for_anonymous(server, prefix):
    exp, pt = _private_files()
    with server.client() as c:
        for p in (exp, pt):
            assert c.get(_file_url(p, prefix)).status_code == 403, p


@pytest.mark.parametrize("prefix", PREFIXES)
def test_private_files_blocked_via_dotdot_and_case(server, prefix):
    exp, pt = _private_files()
    from urllib.parse import quote
    sneaky = [
        str(store.PROJECTS / "demo" / ".." / "demo" / "runs" / "20990101-000000" / "best.pt"),
        str(exp).replace("exports", "EXPORTS"),
    ]
    with server.client() as c:
        for s in sneaky:
            assert c.get(prefix + quote(s.replace("\\", "/"))).status_code == 403, s


@pytest.mark.parametrize("prefix", PREFIXES)
def test_owner_can_still_fetch_private_files(server, prefix):
    exp, pt = _private_files()
    with server.client(cookies=owner_cookies()) as c:
        for p in (exp, pt):
            assert c.get(_file_url(p, prefix)).status_code == 200, p


def test_legacy_file_route_closed_for_anonymous(server):
    # 舊路由本站不用：非站主連一般範例圖都不給（以後 Gradio 再加別名也不會漏）；file= 照常
    img = next((ROOT / "samples" / "shapes").glob("*.png"))
    with server.client() as c:
        assert c.get(_file_url(img, "/gradio_api/file/")).status_code == 403
        assert c.get(_file_url(img)).status_code == 200


def test_labeling_images_still_served_to_anonymous(server):
    # 標註畫面的原圖（專案來源資料夾）不受影響
    img = next(p for p in (ROOT / "samples" / "shapes").glob("*.png"))
    with server.client() as c:
        assert c.get(_file_url(img)).status_code == 200


def test_public_without_google_login_blocks_private_dirs(monkeypatch):
    # 公開站沒開 Google 登入時沒有站主、也掛不了中介層 ⇒ 直接把兩類目錄放進 blocked_paths
    monkeypatch.setattr(store, "PUBLIC", True)
    monkeypatch.setattr(app.auth, "AUTH_ENABLED", False)
    _private_files()
    _, blocked = app._file_access_paths()
    assert str(store.DATA_ROOT / "exports") in blocked
    assert str(store.PROJECTS / "demo" / "runs") in blocked


# ---- allowed / blocked 計算（公開站 fail-safe 預設不開放整顆磁碟） -----------------
def test_public_default_paths_are_scoped(monkeypatch):
    monkeypatch.delenv("SCOPELAB_ALLOWED_PATHS", raising=False)
    monkeypatch.setattr(store, "PUBLIC", True)
    allowed, blocked = app._file_access_paths()
    assert allowed and all(not a.rstrip("/\\").endswith(":") for a in allowed)   # 沒有 C:\ 這種磁碟根
    assert any("samples" in a or "projects" in a or str(store.DATA_ROOT) in a for a in allowed)
    assert any(".git" in b for b in blocked)


def test_exe_exact_path_not_whole_dist(monkeypatch, tmp_path):
    # 假安裝檔放暫存夾：以前直接寫 ROOT/packaging/dist/ScopeLab-Setup.exe 再刪，跑一次測試就把真的安裝檔（313 MB、
    # 在 .gitignore 裡救不回來）換成 2 bytes 再刪掉，公開站的「下載桌面版」因此消失（審查 SL-2）
    real = ROOT / "packaging" / "dist" / "ScopeLab-Setup.exe"
    before = (real.stat().st_size, real.stat().st_mtime_ns) if real.exists() else None
    dist = tmp_path / "dist"
    dist.mkdir()
    exe = dist / "ScopeLab-Setup.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(app, "_SETUP_EXE", exe)
    monkeypatch.setenv("SCOPELAB_ALLOWED_PATHS", str(ROOT))
    allowed, _ = app._file_access_paths()
    assert str(exe) in allowed            # 只允許那一個檔
    assert str(dist) not in allowed       # 不是整個 dist 目錄
    after = (real.stat().st_size, real.stat().st_mtime_ns) if real.exists() else None
    assert after == before                # 真的安裝檔一個位元組都沒動


# ---- 匿名上傳被擋、站主可上傳 ---------------------------------------------------
def _multipart():
    return {"files": ("a.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 32), "image/png")}


def test_anonymous_upload_blocked(server):
    with server.client() as c:
        r = c.post("/gradio_api/upload", files=_multipart())
    assert r.status_code == 403


def test_owner_upload_allowed(server):
    with server.client(cookies=owner_cookies()) as c:
        r = c.post("/gradio_api/upload", files=_multipart())
    assert r.status_code == 200, r.text


# ---- 錄影轉檔／component_server／vibe 端點只給站主（審查 SL-4） ------------------------
# /process_recording 會往暫存夾寫檔再起 ffmpeg 重新編碼（不在佇列、沒有並發上限）；ScopeLab 用不到這幾個端點。
OWNER_ONLY = ["/gradio_api/process_recording", "/gradio_api/component_server",
              "/gradio_api/component_server/", "/gradio_api/vibe-edit", "/gradio_api/vibe-edit/",
              "/gradio_api/undo-vibe-edit"]


def _video():
    return {"video": ("a.mp4", io.BytesIO(b"\x00\x00\x00\x18ftypmp42" + b"0" * 32), "video/mp4")}


@pytest.mark.parametrize("path", OWNER_ONLY)
def test_anonymous_owner_only_posts_blocked(server, path):
    with server.client() as c:
        r = c.post(path, files=_video()) if "recording" in path else c.post(path, json={})
    assert r.status_code == 403, (path, r.status_code, r.text)


def test_owner_process_recording_not_blocked_by_guard(server):
    # 站主不受閘門影響：不帶檔案 → Gradio 自己回 422（驗證失敗），不是閘門的 403，也不會起 ffmpeg
    with server.client(cookies=owner_cookies()) as c:
        r = c.post("/gradio_api/process_recording")
    assert r.status_code != 403, r.text


def test_owner_only_post_matcher():
    from src import auth
    assert auth.owner_only_post("POST", "/gradio_api/process_recording")
    assert auth.owner_only_post("POST", "/gradio_api/upload/")
    # UI 要用的端點照常
    for p in ("/gradio_api/queue/join", "/gradio_api/cancel", "/gradio_api/reset",
              "/gradio_api/stream/abc", "/gradio_api/upload_progress"):
        assert not auth.owner_only_post("POST", p), p
    assert not auth.owner_only_post("GET", "/gradio_api/process_recording")


# ---- 非公開專案的檔案：知道 slug 也不能經 file= 直接拿（審查 SL-3 退回） -------------------
# 正式站 SCOPELAB_ALLOWED_PATHS 是整個 repo 根：projects/<slug>/ 與落在 allowed 內的來源原圖都在服務範圍內，
# 只有白名單（"public": true）專案的檔可以給訪客。
@pytest.fixture()
def private_project():
    import json
    from PIL import Image
    from conftest import DATA
    src = DATA / "srcs" / "priv"
    src.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (199, 10, 10)).save(src / "privimg_0.png")
    pd = DATA / "projects" / "privproj"
    (pd / "edited").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (1, 2, 3)).save(pd / "edited" / "privimg_0.png")
    card = {"slug": "privproj", "name": "私人", "task": "cls", "classes": ["甲"], "source_folder": str(src),
            "created": "2099-01-03", "updated": "2099-01-03"}
    (pd / "card.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    (pd / "labels.json").write_text(json.dumps({"labels": {}, "edits": {}}), encoding="utf-8")
    return {"card": pd / "card.json", "labels": pd / "labels.json",
            "edited": pd / "edited" / "privimg_0.png", "source": src / "privimg_0.png"}


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("kind", ["card", "labels", "edited", "source"])
def test_private_project_files_blocked_for_anonymous(server, private_project, prefix, kind):
    with server.client() as c:
        assert c.get(_file_url(private_project[kind], prefix)).status_code == 403


@pytest.mark.parametrize("prefix", PREFIXES)
def test_private_project_files_blocked_via_dotdot_case_backslash(server, private_project, prefix):
    from urllib.parse import quote
    card = str(private_project["card"])
    sneaky = [
        card.replace("privproj", "demo" + "\\..\\" + "privproj"),     # projects/demo/../privproj/card.json
        card.replace("privproj", "PRIVPROJ"),                          # 大小寫
        card.replace("/", "\\"),                                       # 反斜線
        card.replace("\\", "/"),
    ]
    with server.client() as c:
        for s_ in sneaky:
            assert c.get(prefix + quote(s_)).status_code == 403, s_
            assert c.get(prefix + quote(quote(s_))).status_code in (403, 404), s_   # 二次編碼也不能拿到


@pytest.mark.parametrize("kind", ["card", "labels", "edited", "source"])
def test_owner_can_fetch_private_project_files(server, private_project, kind):
    with server.client(cookies=owner_cookies()) as c:
        assert c.get(_file_url(private_project[kind])).status_code == 200


def test_owner_in_preview_blocked_from_private_project_files(server, private_project):
    from src import auth
    auth.set_local_mode(False)
    with server.client(cookies=owner_cookies()) as c:
        assert c.get(_file_url(private_project["card"])).status_code == 403


def test_public_project_files_still_served_to_anonymous(server, private_project):
    from conftest import DATA, IMAGES
    with server.client() as c:
        for f in (DATA / "projects" / "demo" / "card.json", DATA / "projects" / "demo" / "labels.json",
                  IMAGES / "img_0.png"):
            assert c.get(_file_url(f)).status_code == 200, f


def test_private_file_rules(private_project):
    from conftest import DATA, IMAGES
    assert app._private_file(str(private_project["card"]))
    assert app._private_file(str(private_project["source"]))
    assert app._private_file(str(DATA / "projects" / "not_a_project" / "x.png"))   # 不屬於任何專案：fail-closed
    assert not app._private_file(str(DATA / "projects" / "demo" / "card.json"))
    assert not app._private_file(str(IMAGES / "img_0.png"))


def test_public_without_google_login_blocks_private_projects(monkeypatch, private_project):
    # 沒開登入的公開站沒有檔案閘門：非公開專案的資料夾與來源資料夾直接放進 blocked_paths
    from conftest import DATA, IMAGES
    monkeypatch.setattr(store, "PUBLIC", True)
    monkeypatch.setattr(app.auth, "AUTH_ENABLED", False)
    _, blocked = app._file_access_paths()
    nb = [app._norm(b) for b in blocked]
    assert app._norm(DATA / "projects" / "privproj") in nb
    assert app._norm(DATA / "srcs" / "priv") in nb
    assert app._norm(DATA / "projects" / "demo") not in nb and app._norm(IMAGES) not in nb
