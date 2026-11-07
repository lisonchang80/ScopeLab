"""兩個同名專案不得共用匯出資料夾（審查 SL-9）。

匯出夾用可讀的專案名稱命名（0982d59 的使用者需求，保留）。但 store.unique_slug 允許同名專案共存
（slug 自動加 _2）：以前專案 B 匯出時會跳「已存在舊的匯出 新標註專案__imagefolder…確定要覆蓋嗎？」，
使用者以為是 B 自己的舊匯出，確認後專案 A 的匯出整個被刪掉。
現在目的地 card.json 的 slug 若是另一個專案，改用 <名稱>__<slug>__<格式>，警告講明；同一個專案維持原名。
"""
import json

import pytest

import app
from conftest import DATA, IMAGES, FakeRequest, owner_cookies
from src import exporter, store

NAME = "新標註專案"


def _mk(slug):
    pd = DATA / "projects" / slug
    pd.mkdir(parents=True, exist_ok=True)
    card = {"slug": slug, "name": NAME, "task": "cls", "classes": ["甲", "乙"],
            "source_folder": str(IMAGES), "created": "2099-01-01", "updated": "2099-01-01"}
    (pd / "card.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    labels = {"labels": {str(IMAGES / "img_0.png"): "甲"}, "edits": {}}
    (pd / "labels.json").write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
    return store.load_card(slug), labels


def _export(slug, fmt="cls_folder"):
    card, labels = _mk(slug) if not store.load_card(slug) else (store.load_card(slug), store.load_labels(slug))
    return exporter.run(slug, card, store.scan_folder(str(IMAGES)), labels["labels"], {}, fmt)


def _slug_in(path):
    return json.loads((exporter.Path(path) / "card.json").read_text(encoding="utf-8"))["slug"]


@pytest.mark.parametrize("fmt", ["cls_folder", "cls_zip"])
def test_same_name_other_project_gets_own_folder(fmt):
    _mk("新標註專案")
    _mk("新標註專案_2")
    pa, _, _ = _export("新標註專案", fmt)
    da = exporter.dest_for("新標註專案", fmt, NAME)
    assert da.name == f"{NAME}__imagefolder"                    # 第一個專案維持可讀名稱
    db = exporter.dest_for("新標註專案_2", fmt, NAME)
    assert db != da and db.name == f"{NAME}__新標註專案_2__imagefolder"
    assert exporter.other_owner("新標註專案_2", fmt, NAME) == "新標註專案"
    assert not exporter.already_exists("新標註專案_2", fmt, NAME)   # B 沒有自己的舊匯出 → 不該跳覆蓋警告
    pb, _, _ = _export("新標註專案_2", fmt)
    assert _slug_in(da) == "新標註專案" and _slug_in(db) == "新標註專案_2"   # A 的匯出還在、沒被蓋


def test_same_project_keeps_name_and_overwrite_prompt():
    _mk("新標註專案")
    _export("新標註專案")
    d = exporter.dest_for("新標註專案", "cls_folder", NAME)
    assert d.name == f"{NAME}__imagefolder"
    assert exporter.other_owner("新標註專案", "cls_folder", NAME) is None
    assert exporter.already_exists("新標註專案", "cls_folder", NAME)
    _export("新標註專案")                                         # 再匯一次仍寫原資料夾
    assert d.is_dir() and not list(exporter.EXPORTS.glob(f"{NAME}__新標註專案__*"))


def _confirm(server):
    return server.blocks.fns[server.fn_index("on_export_confirm")].fn


def test_warning_names_the_other_project(server):
    _mk("新標註專案")
    _mk("新標註專案_2")
    _export("新標註專案")
    _export("新標註專案_2")                     # B 的匯出落在帶 slug 的資料夾
    card, labels = store.load_card("新標註專案_2"), store.load_labels("新標註專案_2")
    S = app.new_state(card)
    S["labels"], S["files"] = labels["labels"], store.scan_folder(str(IMAGES))
    out = _confirm(server)("cls_folder", S, FakeRequest(cookies=owner_cookies()), lambda *a, **k: None)
    warn = out[2]["value"]
    assert f"{NAME}__新標註專案_2__imagefolder" in warn
    assert "另一個專案" in warn and "slug=新標註專案" in warn
    assert _slug_in(exporter.dest_for("新標註專案", "cls_folder", NAME)) == "新標註專案"
