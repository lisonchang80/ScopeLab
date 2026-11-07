"""公開站的 allowed_paths 是白名單（審查 GA-4）。

以前正式站 launcher 設 SCOPELAB_ALLOWED_PATHS=<repo 根>，_file_access_paths 原樣採用，只靠 blocked 四項黑名單：
README.md、yolov8s.pt、packaging/、src/、projects/*/card.json（含 source_folder 本機路徑）都能匿名下載，
日後 repo 根新增的祕密檔（.env、env.local.bat 備份）也會直接公開。
現在照 ClinScope _unsafe_to_serve：PUBLIC 時等於、位在其內或涵蓋 repo 根／家目錄的項目剔除並印警告；
固定白名單＝projects/、exports/、samples/、static/、Setup.exe 單檔。
"""
from pathlib import Path

import pytest

import app
from conftest import DATA, IMAGES, ROOT, TMP, owner_cookies
from src import store


def _file_url(p):
    from urllib.parse import quote
    return "/gradio_api/file=" + quote(Path(p).resolve().as_posix())


def _covered(allowed, p):
    p = app._norm(p)
    return any(app._under(p, app._norm(a)) for a in allowed)


@pytest.fixture()
def public(monkeypatch):
    monkeypatch.setattr(store, "PUBLIC", True)
    return monkeypatch


def test_repo_root_dropped_and_defaults_whitelisted(public, capsys):
    public.setenv("SCOPELAB_ALLOWED_PATHS", f"{ROOT};{IMAGES}")
    allowed, _ = app._file_access_paths()
    assert str(ROOT) not in allowed
    assert "SCOPELAB_ALLOWED_PATHS" in capsys.readouterr().out          # 剔除時印警告
    for p in (store.PROJECTS, store.DATA_ROOT / "exports", ROOT / "samples", ROOT / "static", IMAGES):
        assert _covered(allowed, p), p
    for p in (ROOT / "README.md", ROOT / "yolov8s.pt", ROOT / "src" / "auth.py",
              ROOT / "packaging" / "scopelab.spec", ROOT / ".env"):
        assert not _covered(allowed, p), p


@pytest.mark.parametrize("bad", ["root", "inside", "parent", "home", "home_inside"])
def test_unsafe_entries_dropped(public, bad):
    target = {"root": ROOT, "inside": ROOT / "packaging", "parent": ROOT.parent,
              "home": Path.home(), "home_inside": Path.home() / "Desktop"}[bad]
    public.setattr(app, "_HOME_DIR", Path.home())          # 正式站的家目錄規則（conftest 為了暫存夾改過）
    assert app._unsafe_to_serve(target)
    drive = Path(ROOT.anchor)
    assert app._unsafe_to_serve(drive)                     # 涵蓋 repo 根的磁碟根
    public.setenv("SCOPELAB_ALLOWED_PATHS", str(target))
    allowed, _ = app._file_access_paths()
    assert str(target) not in allowed


def test_safe_extra_entry_kept(public):
    extra = TMP / "public_extra"
    extra.mkdir(exist_ok=True)
    public.setenv("SCOPELAB_ALLOWED_PATHS", str(extra))
    allowed, _ = app._file_access_paths()
    assert str(extra) in allowed


def test_no_env_still_whitelist(public):
    public.delenv("SCOPELAB_ALLOWED_PATHS", raising=False)
    allowed, _ = app._file_access_paths()
    assert _covered(allowed, store.DATA_ROOT / "exports" / "x.csv")
    assert not _covered(allowed, ROOT / "README.md")


def test_desktop_mode_unchanged(monkeypatch):
    monkeypatch.setattr(store, "PUBLIC", False)
    monkeypatch.setenv("SCOPELAB_ALLOWED_PATHS", str(ROOT))
    allowed, _ = app._file_access_paths()
    assert [a for a in allowed if a != str(app._SETUP_EXE)] == [str(ROOT)]   # 桌面／開發：env 原樣（只服務本機）


# ---- 經伺服器實際 GET（conftest 的 env 含 repo 根，跟正式站 launcher 一樣） ----------------
@pytest.mark.parametrize("rel", ["README.md", "LICENSE", "src/auth.py", "pytest.ini"])
def test_repo_files_not_served(server, rel):
    with server.client() as c:
        assert c.get(_file_url(ROOT / rel)).status_code == 403, rel
    with server.client(cookies=owner_cookies()) as c:
        assert c.get(_file_url(ROOT / rel)).status_code == 403, rel     # 站主也不從 file= 拿 repo 檔


def test_owner_downloads_csv_and_zip(server):
    """站主下載 CSV／ZIP：exports/ 在白名單內，站主 200、匿名 403。"""
    exp = store.DATA_ROOT / "exports"
    exp.mkdir(parents=True, exist_ok=True)
    (exp / "infer_results_20990101-000000.csv").write_text("a,b\n", encoding="utf-8")
    (exp / "合成示範__imagefolder.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    for f in ("infer_results_20990101-000000.csv", "合成示範__imagefolder.zip"):
        with server.client(cookies=owner_cookies()) as c:
            assert c.get(_file_url(exp / f)).status_code == 200, f
        with server.client() as c:
            assert c.get(_file_url(exp / f)).status_code == 403, f


def test_samples_still_served(server):
    img = next((ROOT / "samples" / "shapes").glob("*.png"))
    with server.client() as c:
        assert c.get(_file_url(img)).status_code == 200
