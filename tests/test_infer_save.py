"""推論頁「💾 另存資料夾」與「匯出 CSV」不刪舊結果、失敗要報數（審查 SL-8）。

以前每次另存都先 rmtree(exports/infer_sorted, ignore_errors=True)：上一批結果被整個刪掉、沒有提示；
被鎖的檔刪不掉時新舊兩批混在同一夾；複製失敗用 except: pass 吞掉，toast 只報成功張數。
CSV 固定覆蓋 exports/infer_results.csv。現在每次寫到帶時間戳的新資料夾／新檔名，舊的不動。
新檔名仍落在 exports/ 底下：非站主照樣不能經 /gradio_api/file= 下載（第三輪的檔案閘門）。
"""
from pathlib import Path
from urllib.parse import quote

import gradio as gr
import pytest

from conftest import DATA, IMAGES, FakeRequest, owner_cookies
from src.infer import tab as infer_tab


def _fn(server, qualname):
    return server.blocks.fns[server.fn_index(qualname)].fn


def _raw(paths, cls="甲"):
    return {"task": "cls", "classes": ["甲", "乙"], "ov": {},
            "res": [{"path": str(p), "ok": True, "top": [(cls, 0.9)]} for p in paths]}


@pytest.fixture()
def toasts(monkeypatch):
    got = []
    monkeypatch.setattr(gr, "Info", lambda m, *a, **k: got.append(("info", m)))
    monkeypatch.setattr(gr, "Warning", lambda m, *a, **k: got.append(("warn", m)))
    return got


def _sorted_dirs():
    return sorted((DATA / "exports").glob("infer_sorted_*"))


def test_save_twice_keeps_previous_batch(server, toasts):
    on_save = _fn(server, "build_infer_tab.<locals>.on_save")
    owner = FakeRequest(cookies=owner_cookies())
    on_save(_raw([IMAGES / "img_0.png", IMAGES / "img_1.png"]), 0.5, owner)
    on_save(_raw([IMAGES / "img_2.png"], "乙"), 0.5, owner)
    dirs = _sorted_dirs()
    assert len(dirs) == 2, dirs
    a, b = dirs
    assert sorted(p.name for p in a.rglob("*.png")) == ["img_0.png", "img_1.png"]
    assert [p.relative_to(b).as_posix() for p in b.rglob("*.png")] == ["乙/img_2.png"]
    assert not (DATA / "exports" / "infer_sorted").exists()


def test_save_reports_success_and_failure_counts(server, toasts):
    on_save = _fn(server, "build_infer_tab.<locals>.on_save")
    raw = _raw([IMAGES / "img_0.png", IMAGES / "missing.png"])
    on_save(raw, 0.5, FakeRequest(cookies=owner_cookies()))
    msgs = " ".join(m for _, m in toasts)
    assert "成功 1" in msgs and "失敗 1" in msgs, toasts
    assert any(kind == "warn" for kind, _ in toasts), toasts     # 有失敗要用 Warning，不是 Info
    assert "missing.png" in msgs


def test_save_sorted_helper_counts():
    ok, fail, dest, failed = infer_tab._save_sorted(_raw([IMAGES / "img_0.png", IMAGES / "nope.png"]), 0.5)
    assert (ok, fail) == (1, 1) and failed == ["nope.png"]
    assert Path(dest).name.startswith("infer_sorted_") and Path(dest).is_dir()


def test_csv_writes_new_file_each_time(server, toasts):
    on_csv = _fn(server, "build_infer_tab.<locals>.on_csv")
    owner = FakeRequest(cookies=owner_cookies())
    u1 = on_csv(_raw([IMAGES / "img_0.png"]), 0.5, owner)
    u2 = on_csv(_raw([IMAGES / "img_1.png"]), 0.5, owner)
    p1, p2 = Path(u1["value"]), Path(u2["value"])
    assert p1 != p2 and p1.is_file() and p2.is_file()
    assert p1.name.startswith("infer_results_") and p1.suffix == ".csv"
    assert "img_0.png" in p1.read_text(encoding="utf-8-sig")
    assert not (DATA / "exports" / "infer_results.csv").exists()


def _file_url(p):
    return "/gradio_api/file=" + quote(Path(p).resolve().as_posix())


@pytest.mark.parametrize("prefix", ["/gradio_api/file=", "/gradio_api/file/"])
def test_timestamped_outputs_still_owner_only(server, toasts, prefix):
    owner = FakeRequest(cookies=owner_cookies())
    _fn(server, "build_infer_tab.<locals>.on_save")(_raw([IMAGES / "img_0.png"]), 0.5, owner)
    csv = Path(_fn(server, "build_infer_tab.<locals>.on_csv")(_raw([IMAGES / "img_0.png"]), 0.5, owner)["value"])
    img = next(_sorted_dirs()[-1].rglob("img_0.png"))
    for p in (csv, img):
        url = prefix + quote(p.resolve().as_posix())
        with server.client() as c:
            assert c.get(url).status_code == 403, p
    for p in (csv, img):
        with server.client(cookies=owner_cookies()) as c:
            assert c.get(_file_url(p)).status_code == 200, p
