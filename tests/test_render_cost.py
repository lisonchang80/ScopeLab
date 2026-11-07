"""「詳細資料」檢視（影像格隱藏）改分類時，不得重送整頁影像格（審查 SL-12）。

render4 以前一律回 gr.update(value=items, visible=show_gallery)：影像格隱藏時 Gradio 照樣把整頁每張圖
整檔 sha256 一次（processing_utils.hash_file）、第一次還複製進快取；2,000 張的資料集逐張改分類，每按一次
就重讀整份資料集。現在隱藏時只送 visible=False，切回「圖示」才送 value。
"""
import json

import pytest
from gradio import processing_utils

import app
from conftest import IMAGES, owner_cookies
from src import store


def _S(view):
    S = app.new_state(store.load_card("demo"))
    S["files"] = store.scan_folder(str(IMAGES))
    S["view"] = view
    return S


def test_hidden_gallery_update_has_no_value():
    upd = app.render4(_S("詳細資料"))[1]
    assert upd.get("visible") is False
    assert "value" not in upd, upd


def test_icon_view_sends_value_and_page_files():
    S = _S("圖示")
    upd = app.render4(S)[1]
    assert upd.get("visible") is True
    assert [p for p, _ in upd["value"]] == S["page_files"] == S["files"]


@pytest.fixture()
def hash_calls(monkeypatch):
    calls = []
    orig = processing_utils.hash_file

    def counting(*a, **k):
        calls.append(a[0] if a else k)
        return orig(*a, **k)
    monkeypatch.setattr(processing_utils, "hash_file", counting)
    return calls


def test_cls_event_in_detail_view_hashes_nothing(server, hash_calls):
    sess = "render-cost"
    ok, out = server.call("on_proj_change", ["demo", None], cookies=owner_cookies(), session=sess)
    assert ok, out
    hash_calls.clear()
    obj = {"a": "cls", "i": 0, "p": "甲"}
    ok, out = server.call("on_bridge", [json.dumps(obj, ensure_ascii=False), None],
                          cookies=owner_cookies(), session=sess)
    assert ok, out
    gal = out["data"][2]
    assert isinstance(gal, dict) and gal.get("visible") is False and "value" not in gal, gal
    assert hash_calls == [], hash_calls


def test_switch_to_icon_view_sends_gallery(server, hash_calls):
    sess = "render-cost-icon"
    ok, _ = server.call("on_proj_change", ["demo", None], cookies=owner_cookies(), session=sess)
    assert ok
    hash_calls.clear()
    ok, out = server.call("on_view", ["👁 圖示", None], cookies=owner_cookies(), session=sess)
    assert ok, out
    gal = out["data"][2]
    assert gal.get("visible") is True and len(gal.get("value") or []) == 3, gal
