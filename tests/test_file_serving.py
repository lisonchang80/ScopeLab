"""Gradio /gradio_api/file= 的曝險範圍，與匿名上傳防護。"""
import io

import pytest

import app
from conftest import OWNER, ROOT, owner_cookies
from src import store


def _file_url(abs_path):
    from urllib.parse import quote
    from pathlib import Path
    return "/gradio_api/file=" + quote(Path(abs_path).resolve().as_posix())


# ---- 敏感檔不得被服務（blocked_paths 優先） ------------------------------------
@pytest.mark.parametrize("rel", [".git/config", ".git/HEAD"])
def test_git_dir_blocked(server, rel):
    with server.client() as c:
        r = c.get(_file_url(ROOT / rel))
    assert r.status_code == 403, rel


def test_env_local_and_idlog_blocked(server, tmp_path, monkeypatch):
    # 這些檔在正式站存在且含祕密；測試環境造一個臨時 env.local.bat 驗證它落在 blocked 內。
    envf = ROOT / "packaging" / "autostart" / "env.local.bat"
    created = False
    if not envf.exists():
        envf.write_text("set SECRET=x\n"); created = True
    try:
        with server.client() as c:
            r = c.get(_file_url(envf))
        assert r.status_code == 403
    finally:
        if created:
            envf.unlink()


# ---- 正常檔仍可服務（沒有過度封鎖） --------------------------------------------
def test_sample_image_served(server):
    img = next((ROOT / "samples" / "shapes").glob("*.png"))
    with server.client() as c:
        r = c.get(_file_url(img))
    assert r.status_code == 200


# ---- allowed / blocked 計算（公開站 fail-safe 預設不開放整顆磁碟） -----------------
def test_public_default_paths_are_scoped(monkeypatch):
    monkeypatch.delenv("SCOPELAB_ALLOWED_PATHS", raising=False)
    monkeypatch.setattr(store, "PUBLIC", True)
    allowed, blocked = app._file_access_paths()
    assert allowed and all(not a.rstrip("/\\").endswith(":") for a in allowed)   # 沒有 C:\ 這種磁碟根
    assert any("samples" in a or "projects" in a or str(store.DATA_ROOT) in a for a in allowed)
    assert any(".git" in b for b in blocked)


def test_exe_exact_path_not_whole_dist(monkeypatch, tmp_path):
    dist = ROOT / "packaging" / "dist"
    exe = dist / "ScopeLab-Setup.exe"
    created_dir = not dist.exists()
    dist.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"MZ")
    try:
        monkeypatch.setenv("SCOPELAB_ALLOWED_PATHS", str(ROOT))
        allowed, _ = app._file_access_paths()
        assert str(exe) in allowed            # 只允許那一個檔
        assert str(dist) not in allowed       # 不是整個 dist 目錄
    finally:
        exe.unlink()
        if created_dir:
            dist.rmdir()


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
