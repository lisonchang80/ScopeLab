"""吃本機 GPU / 讀本機資料夾 / 刪專案的事件：訪客直接呼叫一律被拒、站主照常。

torch、trainer、推論引擎、原生選夾對話框全部 stub 掉：測試不訓練、不推論、不開視窗，
只驗「閘門有沒有在 handler 內、fail-closed」。
"""
import json
import sys
import types

import pytest

import app
from conftest import DATA, IMAGES, SLUG, TMP, FakeRequest, guest_cookies, owner_cookies
from src import auth
from src.infer import tab as infer_tab

GUESTS = [pytest.param(None, id="anonymous"), pytest.param("guest", id="non-owner-login")]


def _cookies(who):
    return owner_cookies() if who == "owner" else (guest_cookies() if who == "guest" else None)


@pytest.fixture()
def engines(monkeypatch):
    """假 device / trainer / predictor：記錄有沒有被叫到（被叫到 = 閘門沒擋住）。"""
    calls = []
    device = types.ModuleType("src.train.device")
    device.pick_device = lambda: calls.append("pick_device") or {"device": "cpu", "name": "CPU", "vram_gb": 0.0}
    device.device_summary = lambda d: "CPU"

    trainer = types.ModuleType("src.train.trainer_cls")

    def train_cls(name, files, labels, classes, recipe, dev, out_dir):
        calls.append("train_cls")
        yield {"phase": "done", "weights": f"{out_dir}/best.pt", "model_card": f"{out_dir}/model_card.json",
               "summary": {"best_val_acc": 1.0, "arch": "stub", "epochs": 1}}
    trainer.train_cls = train_cls

    predictor = types.ModuleType("src.infer.predictor")
    predictor.load_model = lambda w, d: calls.append("load_model") or (None, ["甲", "乙"], 32)
    predictor.predict = lambda m, c, i, paths, d: [{"path": p, "ok": True, "top": [("甲", 0.9)]} for p in paths]

    import src.infer
    import src.train
    for name, mod, pkg, attr in (("src.train.device", device, src.train, "device"),
                                 ("src.train.trainer_cls", trainer, src.train, "trainer_cls"),
                                 ("src.infer.predictor", predictor, src.infer, "predictor")):
        monkeypatch.setitem(sys.modules, name, mod)
        monkeypatch.setattr(pkg, attr, mod, raising=False)

    monkeypatch.setattr(app, "_native_dir", lambda: calls.append("native_dir") or str(IMAGES))
    monkeypatch.setattr(infer_tab, "_pick_dir", lambda: calls.append("pick_dir") or str(IMAGES))
    return calls


@pytest.fixture()
def fake_run():
    run = TMP / "run_stub"
    run.mkdir(exist_ok=True)
    (run / "best.pt").write_bytes(b"")
    (run / "model_card.json").write_text(json.dumps({"task": "cls"}), encoding="utf-8")
    return str(run / "best.pt")


def _fn(server, qualname):
    return server.blocks.fns[server.fn_index(qualname)].fn


def _noprogress(*a, **k):
    return None


# ---- 訓練 --------------------------------------------------------------------
@pytest.mark.parametrize("who", GUESTS)
def test_train_rejected_for_guest(server, engines, who):
    ok, _ = server.call("build_train_tab.<locals>.on_train", [SLUG, "fast"], cookies=_cookies(who))
    assert not ok
    assert engines == []


def test_train_runs_for_owner(server, engines):
    ok, out = server.call("build_train_tab.<locals>.on_train", [SLUG, "fast"], cookies=owner_cookies())
    assert ok, out
    assert "train_cls" in engines


# ---- 選資料夾（伺服器端原生對話框） ---------------------------------------------
@pytest.mark.parametrize("who", GUESTS)
def test_pick_folder_rejected_for_guest(server, engines, who):
    server.call("on_pick_folder", [None], cookies=_cookies(who))
    server.call("build_infer_tab.<locals>.on_pick", [], cookies=_cookies(who))
    assert engines == []


def test_pick_folder_ok_for_owner(server, engines):
    ok, out = server.call("on_pick_folder", [None], cookies=owner_cookies())
    assert ok, out
    ok, out = server.call("build_infer_tab.<locals>.on_pick", [], cookies=owner_cookies())
    assert ok, out
    assert engines.count("native_dir") == 1 and engines.count("pick_dir") == 1


# ---- 批次 / 單張推論、另存資料夾 ------------------------------------------------
def test_infer_rejected_for_guest(server, engines, fake_run):
    on_infer = _fn(server, "build_infer_tab.<locals>.on_infer")
    on_single = _fn(server, "build_infer_tab.<locals>.on_single")
    plist = [str(IMAGES / "img_0.png")]
    for req in (FakeRequest(), FakeRequest(cookies=guest_cookies())):
        on_infer(fake_run, plist, 0.5, req, _noprogress)
        raw = {"task": "cls", "res": [{"path": plist[0], "ok": True, "top": [("甲", 0.9)]}], "ov": {}}
        on_single(fake_run, raw, 0, 0.5, req)
    assert engines == []


def test_infer_runs_for_owner(server, engines, fake_run):
    on_infer = _fn(server, "build_infer_tab.<locals>.on_infer")
    out = on_infer(fake_run, [str(IMAGES / "img_0.png")], 0.5, FakeRequest(cookies=owner_cookies()), _noprogress)
    assert out[4]["res"][0]["top"][0][0] == "甲"
    assert "load_model" in engines


def test_save_sorted_rejected_for_guest(server):
    on_save = _fn(server, "build_infer_tab.<locals>.on_save")
    raw = {"task": "cls", "res": [{"path": str(IMAGES / "img_0.png"), "ok": True, "top": [("甲", 0.9)]}], "ov": {}}
    on_save(raw, 0.5, FakeRequest())
    assert not (DATA / "exports" / "infer_sorted").exists()
    on_save(raw, 0.5, FakeRequest(cookies=owner_cookies()))
    assert (DATA / "exports" / "infer_sorted").is_dir()


# ---- 刪除專案 ------------------------------------------------------------------
@pytest.mark.parametrize("who", GUESTS)
def test_delete_rejected_for_guest(server, who):
    ok, _ = server.call("on_del_confirm", [SLUG, None], cookies=_cookies(who))
    assert not ok
    assert (DATA / "projects" / SLUG / "card.json").is_file()


def test_delete_ok_for_owner(server):
    ok, out = server.call("on_del_confirm", [SLUG, None], cookies=owner_cookies())
    assert ok, out
    assert not (DATA / "projects" / SLUG).exists()


def test_request_none_is_guest():
    """沒有 request（非 Gradio 呼叫）一律當訪客：fail-closed。"""
    assert not auth.is_owner(None)
    assert auth.public_locked(None)
