"""站內訓練吃的資料要和匯出一致（審查 SL-5）：編輯器裁切／旋轉後的影像＋原圖 stem 檔名＋同一份納入範圍。

以前 on_train 只讀 labels.keys()，trainer_det 複製原圖、trainer_cls 讀原圖；但編輯器把框換算成
「相對裁切後影像」的座標再存回，所以 YOLO 拿整張原圖配裁切座標，框落在錯的位置。
labels.json 裡舊資料夾留下的標記也被靜默納入，標記頁計數和匯出都不算它們。

不訓練、不 import torch：device／trainer 模組以 stub 取代，只跑到建資料集為止。
"""
import importlib
import json
import sys
import types

import pytest
from PIL import Image

from conftest import DATA, TMP, FakeRequest, owner_cookies
from src import exporter, imaging, store
from src.train import dataset

RED, BLUE = (220, 20, 20), (20, 20, 220)


def _setup_det(slug="det_proj"):
    """原圖 400x200（左半紅、右半藍）；編輯版＝右半裁切 200x200（全藍），框畫在編輯版的中央。"""
    src = TMP / f"{slug}_src"
    src.mkdir(parents=True, exist_ok=True)
    for p in src.iterdir():
        p.unlink()
    a, b = src / "a.jpg", src / "b.jpg"
    for p in (a, b):
        im = Image.new("RGB", (400, 200), RED)
        im.paste(BLUE, (200, 0, 400, 200))
        im.save(p, quality=95)
    pd = DATA / "projects" / slug
    pd.mkdir(parents=True, exist_ok=True)
    card = {"slug": slug, "name": "偵測裁切", "task": "det", "classes": ["細胞"],
            "source_folder": str(src), "created": "2099-01-01", "updated": "2099-01-01"}
    (pd / "card.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    edited = store.edited_dir(slug) / "a__edited.png"
    Image.open(a).crop((200, 0, 400, 200)).save(edited)
    box = [{"cls": "細胞", "x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}]
    labels = {str(a): box, str(b): box,
              str(TMP / "old_folder" / "z.jpg"): box}           # 舊資料夾留下的標記
    data = {"labels": labels, "edits": {str(a): str(edited)}}
    (pd / "labels.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return slug, card, data, a, b, edited


def test_scope_matches_export_and_counts_stale():
    slug, card, data, a, b, _ = _setup_det()
    sc = dataset.training_scope(card, data)
    assert sc["files"] == store.scan_folder(card["source_folder"]) == [str(a), str(b)]
    assert sc["stale"] == 1
    assert sc["source_ok"] is True
    assert sc["edits"] == data["edits"]


def test_scope_missing_source_folder():
    sc = dataset.training_scope({"source_folder": str(TMP / "nope")}, {"labels": {"x": "甲"}})
    assert sc["files"] == [] and sc["source_ok"] is False and sc["stale"] == 1


def test_det_dataset_uses_edited_image_and_original_stem(tmp_path):
    slug, card, data, a, b, edited = _setup_det()
    sc = dataset.training_scope(card, data)
    yp, split = dataset.build_det_dataset(sc["files"], sc["labels"], card["classes"],
                                          tmp_path / "ds", sc["edits"])
    assert split == (1, 1)
    imgs = {p.name: p for p in (tmp_path / "ds" / "images").rglob("*") if p.is_file()}
    lbls = {p.name: p for p in (tmp_path / "ds" / "labels").rglob("*.txt")}
    assert set(imgs) == {"a.png", "b.jpg"}          # a 用編輯版（png）、檔名仍是原圖 stem
    assert set(lbls) == {"a.txt", "b.txt"}
    ia = Image.open(imgs["a.png"]).convert("RGB")
    assert ia.size == (200, 200)                    # 編輯版尺寸，不是原圖 400x200
    # 框中心（0.5, 0.5）在訓練影像上要落在藍色區（編輯後影像），原圖同一位置是紅藍交界
    line = lbls["a.txt"].read_text(encoding="utf-8").split()
    cx, cy = float(line[1]), float(line[2])
    px = ia.getpixel((int(cx * ia.width), int(cy * ia.height)))
    assert px[2] > 150 and px[0] < 80, px
    # 與匯出結果逐位元組一致（同一份影像內容）
    assert imgs["a.png"].read_bytes() == edited.read_bytes()


def test_cls_items_use_edited_image():
    src = TMP / "cls_src"
    src.mkdir(exist_ok=True)
    p = src / "c.png"
    Image.new("RGB", (40, 20), RED).save(p)
    e = store.edited_dir("demo") / "c__x.png"
    Image.new("RGB", (20, 20), BLUE).save(e)
    items = dataset.cls_items([str(p)], {str(p): "乙"}, ["甲", "乙"], {str(p): str(e)})
    assert items == [(str(e), 1)]
    assert items[0][0] == imaging.display_path(str(p), {str(p): str(e)})


# ---- on_train / trainer_det 走同一份資料（stub 掉 torch 相關模組）----------------------------

@pytest.fixture()
def stub_device(monkeypatch):
    import src.train as pkg
    dev = types.ModuleType("src.train.device")
    dev.pick_device = lambda: {"device": "cpu", "name": "CPU", "vram_gb": 0.0}
    dev.device_summary = lambda d: "CPU"
    dev.gpu_stats = lambda: {}
    dev.health_warnings = lambda s: []
    dev.batch_for = lambda *a: 4
    monkeypatch.setitem(sys.modules, "src.train.device", dev)
    monkeypatch.setattr(pkg, "device", dev, raising=False)
    yield dev
    for name in ("trainer_det", "trainer_cls"):
        sys.modules.pop(f"src.train.{name}", None)
        if hasattr(pkg, name):
            delattr(pkg, name)


def test_trainer_det_builds_dataset_from_edited_images(stub_device, tmp_path):
    slug, card, data, a, b, edited = _setup_det()
    sys.modules.pop("src.train.trainer_det", None)
    td = importlib.import_module("src.train.trainer_det")
    sc = dataset.training_scope(card, data)
    gen = td.train_det("x", sc["files"], sc["labels"], card["classes"], "fast",
                       {"device": "cpu"}, str(tmp_path / "run"), edits=sc["edits"])
    evs = []
    for ev in gen:                      # 只跑到 data 事件（之後才 import ultralytics）
        evs.append(ev)
        if ev["phase"] in ("data", "error"):
            break
    assert evs[-1]["phase"] == "data", evs
    im = Image.open(next((tmp_path / "run" / "dataset" / "images").rglob("b.jpg")))
    names = sorted(p.name for p in (tmp_path / "run" / "dataset" / "images").rglob("*.*"))
    assert names == ["a.png", "b.jpg"] and im.size == (400, 200)
    ia = next((tmp_path / "run" / "dataset" / "images").rglob("a.png"))
    assert Image.open(ia).size == (200, 200)


def _on_train(server):
    fns = [bf.fn for bf in server.blocks.fns.values()
           if bf.fn is not None and getattr(bf.fn, "__qualname__", "").endswith("build_train_tab.<locals>.on_train")]
    assert len(fns) == 1
    return fns[0]


def test_on_train_passes_scope_and_edits(server, stub_device):
    import src.train as pkg
    slug, card, data, a, b, edited = _setup_det()
    got = {}

    def fake_train_det(name, files, labels, classes, recipe_key, dev, out_dir, **kw):
        got.update(files=files, labels=labels, kw=kw)
        yield {"phase": "data", "n_train": 1, "n_val": 1, "n_classes": 1}
        yield {"phase": "error", "msg": "stub"}

    fake = types.ModuleType("src.train.trainer_det")
    fake.train_det = fake_train_det
    sys.modules["src.train.trainer_det"] = fake        # stub_device 收尾時一併移除
    pkg.trainer_det = fake

    outs = list(_on_train(server)(slug, "fast", request=FakeRequest(cookies=owner_cookies()),
                                  progress=lambda *a, **k: None))
    assert got["files"] == [str(a), str(b)]                     # 來源資料夾外的舊標記不納入
    assert got["kw"].get("edits") == data["edits"]
    eplog = "".join(str(o[5]) for o in outs)
    assert "舊標記 1 筆" in eplog


def test_export_and_training_use_same_image_bytes(tmp_path):
    """同一張圖：匯出 YOLO 與站內訓練資料集的影像內容相同。"""
    slug, card, data, a, b, edited = _setup_det()
    sc = dataset.training_scope(card, data)
    dest = tmp_path / "exp"
    exporter.export_det_yolo(slug, card, sc["files"], sc["labels"], sc["edits"], dest)
    dataset.build_det_dataset(sc["files"], sc["labels"], card["classes"], tmp_path / "ds", sc["edits"])
    tr = next((tmp_path / "ds" / "images").rglob("a.*"))
    ex = dest / "images" / "a.jpg"
    assert tr.read_bytes() == ex.read_bytes()
    assert (dest / "labels" / "a.txt").read_text(encoding="utf-8") == \
        next((tmp_path / "ds" / "labels").rglob("a.txt")).read_text(encoding="utf-8")
