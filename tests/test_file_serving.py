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
