"""寫檔事件的伺服器端把關：訪客（匿名 / 非站主帳號）直接呼叫 API 也不能寫磁碟；站主照常。

UI 上這些按鈕對訪客是虛化的，但 Gradio 每個事件都能經 /gradio_api/queue/join 直接觸發，
所以權威檢查必須在 handler 函式內（fail-closed）。
"""
import base64
import io
import json

import pytest
from PIL import Image

import app
from conftest import (DATA, IMAGES, SLUG, FakeRequest, guest_cookies, owner_cookies,
                      read_card, read_labels)
from src import auth

GUESTS = [pytest.param(None, id="anonymous"), pytest.param("guest", id="non-owner-login")]


def _cookies(who):
    return owner_cookies() if who == "owner" else (guest_cookies() if who == "guest" else None)


def _png_dataurl():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 0, 0)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _bridge(server, obj, who, session=None):
    return server.call("on_bridge", [json.dumps(obj, ensure_ascii=False), None],
                       cookies=_cookies(who), session=session)


# ---- 存基本資料（建/改專案 card.json） ----------------------------------------
@pytest.mark.parametrize("who", GUESTS)
def test_save_card_rejected_for_guest(server, who):
    before = read_card()
    ok, _ = server.call("on_save_card", ["改名", "", "cls", "", "0.1.0", "", "", "x、y", None],
                        cookies=_cookies(who))
    assert not ok
    assert read_card() == before
    assert sorted(p.name for p in (DATA / "projects").iterdir()) == [SLUG]


def test_save_card_ok_for_owner(server):
    ok, out = server.call("on_save_card", ["改名", "", "cls", "", "0.1.0", "", "", "x、y", None],
                          cookies=owner_cookies())
    assert ok, out
    assert read_card()["name"] == "改名"
    assert read_card()["classes"] == ["x", "y"]


def test_save_card_rejected_for_owner_in_preview(server):
    auth.set_local_mode(False)            # 站主取消「本地模式」= 預覽訪客唯讀
    ok, _ = server.call("on_save_card", ["改名", "", "cls", "", "0.1.0", "", "", "", None],
                        cookies=owner_cookies())
    assert not ok
    assert read_card()["name"] != "改名"


# ---- 詳細表改分類 / 工具列指派 / 加類別（labels.json / card.json） -----------------
@pytest.mark.parametrize("who", GUESTS)
def test_bridge_cls_not_persisted_for_guest(server, who):
    ok, _ = _bridge(server, {"a": "cls", "i": 0, "p": "甲"}, who)
    assert ok
    assert read_labels()["labels"] == {}


def test_bridge_cls_persisted_for_owner(server):
    ok, out = _bridge(server, {"a": "cls", "i": 0, "p": "甲"}, "owner")
    assert ok, out
    assert read_labels()["labels"] == {str(IMAGES / "img_0.png"): "甲"}


@pytest.mark.parametrize("who", GUESTS)
def test_assign_not_persisted_for_guest(server, who):
    sess = "assign-" + str(who)
    _bridge(server, {"a": "sel", "i": 1}, who, session=sess)
    ok, _ = server.call("on_assign", ["乙", None], cookies=_cookies(who), session=sess)
    assert ok
    assert read_labels()["labels"] == {}


def test_assign_persisted_for_owner(server):
    _bridge(server, {"a": "sel", "i": 1}, "owner", session="assign-owner")
    ok, out = server.call("on_assign", ["乙", None], cookies=owner_cookies(), session="assign-owner")
    assert ok, out
    assert read_labels()["labels"] == {str(IMAGES / "img_1.png"): "乙"}


@pytest.mark.parametrize("who", GUESTS)
def test_addclass_not_persisted_for_guest(server, who):
    _bridge(server, {"a": "addclass", "name": "丙"}, who)
    assert read_card()["classes"] == ["甲", "乙"]


def test_addclass_persisted_for_owner(server):
    ok, out = _bridge(server, {"a": "addclass", "name": "丙"}, "owner")
    assert ok, out
    assert read_card()["classes"] == ["甲", "乙", "丙"]


# ---- 編輯器存回（edited/*.png + labels.json） ---------------------------------
@pytest.mark.parametrize("who", GUESTS)
def test_editor_save_writes_nothing_for_guest(server, who):
    _bridge(server, {"a": "editor_save", "path": str(IMAGES / "img_0.png"),
                     "img": _png_dataurl(), "boxes": []}, who)
    edited = DATA / "projects" / SLUG / "edited"
    assert not edited.exists() or not any(edited.iterdir())
    assert read_labels()["edits"] == {}


def test_editor_save_ok_for_owner(server):
    ok, out = _bridge(server, {"a": "editor_save", "path": str(IMAGES / "img_0.png"),
                               "img": _png_dataurl(), "boxes": []}, "owner")
    assert ok, out
    edits = read_labels()["edits"]
    assert list(edits) == [str(IMAGES / "img_0.png")]


# ---- 匯出資料集（exports/，會刪舊匯出再複製 / 壓縮） ------------------------------
@pytest.mark.parametrize("who", GUESTS)
def test_export_rejected_for_guest(server, who):
    ok, _ = server.call("on_export_confirm", ["cls_folder", None], cookies=_cookies(who))
    assert not ok
    assert not (DATA / "exports").exists()


def test_export_ok_for_owner(server):
    ok, out = server.call("on_export_confirm", ["cls_folder", None], cookies=owner_cookies())
    assert ok, out
    assert (DATA / "exports").is_dir()


# ---- 標記頁「開始訓練」（建 runs/） ---------------------------------------------
@pytest.mark.parametrize("who", GUESTS)
def test_start_train_rejected_for_guest(server, who):
    ok, _ = server.call("on_start_train", [None], cookies=_cookies(who))
    assert not ok
    assert not (DATA / "projects" / SLUG / "runs").exists()


def test_start_train_ok_for_owner(server):
    ok, out = server.call("on_start_train", [None], cookies=owner_cookies())
    assert ok, out
    assert (DATA / "projects" / SLUG / "runs").is_dir()


# ---- 推論頁「匯出 CSV」（exports/infer_results.csv） ------------------------------
def _on_csv(server):
    return server.blocks.fns[server.fn_index("build_infer_tab.<locals>.on_csv")].fn


RAW = {"task": "cls", "classes": ["甲"], "ov": {},
       "res": [{"path": str(IMAGES / "img_0.png"), "ok": True, "top": [("甲", 0.9)]}]}


def test_csv_rejected_for_guest(server):
    _on_csv(server)(RAW, 0.5, FakeRequest())
    assert not (DATA / "exports" / "infer_results.csv").exists()


def test_csv_ok_for_owner(server):
    _on_csv(server)(RAW, 0.5, FakeRequest(cookies=owner_cookies()))
    assert (DATA / "exports" / "infer_results.csv").is_file()


def test_desktop_mode_never_locked(monkeypatch):
    """桌面版 / 開發（非 PUBLIC）：沒有登入也一律可寫（放行捷徑只在非公開模式）。"""
    monkeypatch.setattr(app.store, "PUBLIC", False)
    assert not auth.public_locked(FakeRequest())
