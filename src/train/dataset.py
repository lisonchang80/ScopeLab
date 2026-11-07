"""ScopeLab — 準備訓練資料（純檔案處理、不 import torch，可單獨測；審查 SL-5）。

站內訓練要和「標記」頁匯出吃同一份資料：

- 納入範圍＝card.source_folder 目前掃得到的影像（store.scan_folder），和標記頁計數、匯出用的
  S["files"] 同一份。labels.json 裡留著的舊資料夾標記不納入，筆數回報給 UI。
- 影像內容＝編輯器裁切／旋轉／翻轉後的版本（imaging.display_path）。編輯器把框換算成
  「相對編輯後影像」的 0–1 座標再存回，所以拿原圖配這組框會錯位。
- 資料集裡的檔名用原圖 stem（與匯出的 labels/<stem>.txt 一致），副檔名跟著實際影像走。
"""
from __future__ import annotations

import random
import shutil
from pathlib import Path

import yaml

from .. import store
from ..imaging import display_path


def training_scope(card: dict, data: dict) -> dict:
    """回 {"files", "labels", "edits", "stale", "source_ok"}。

    files：來源資料夾目前的影像（原圖路徑，labels／edits 的鍵）；
    stale：labels 裡不在 files 內、因此不納入訓練的舊標記筆數；
    source_ok：來源資料夾是否存在。
    """
    labels = dict((data or {}).get("labels") or {})
    edits = dict((data or {}).get("edits") or {})
    src = (card or {}).get("source_folder") or ""
    source_ok = bool(src) and Path(src).is_dir()
    files = store.scan_folder(src) if source_ok else []
    inside = set(files)
    stale = sum(1 for k, v in labels.items() if k not in inside and v)
    return {"files": files, "labels": labels, "edits": edits, "stale": stale, "source_ok": source_ok}


def cls_items(files, labels, classes, edits=None) -> list[tuple[str, int]]:
    """分類訓練用的 (影像路徑, 類別索引)：影像是編輯版（有的話），與 export_cls 一致。"""
    return [(display_path(p, edits), classes.index(labels[p])) for p in files
            if isinstance(labels.get(p), str) and labels.get(p) in classes]


def build_det_dataset(files, labels, classes, ds_dir: Path, edits=None):
    """把 det labels 寫成 YOLO 資料集；回 (data.yaml 路徑, (n_train, n_val))，不足回 (None, None)。

    影像複製編輯版（display_path），檔名用原圖 stem，與 exporter.export_det_yolo 一致。
    """
    items = [(p, labels[p]) for p in files if isinstance(labels.get(p), list) and labels[p]]
    if len(items) < 2:
        return None, None
    random.Random(0).shuffle(items)
    k = max(1, int(len(items) * 0.2))
    val, train = items[:k], (items[k:] or items[:k])
    cidx = {c: i for i, c in enumerate(classes)}
    for split, group in (("train", train), ("val", val)):
        imgd = ds_dir / "images" / split
        lbld = ds_dir / "labels" / split
        imgd.mkdir(parents=True, exist_ok=True)
        lbld.mkdir(parents=True, exist_ok=True)
        for p, boxes in group:
            srcp = display_path(p, edits)
            shutil.copy2(srcp, imgd / (Path(p).stem + Path(srcp).suffix))
            lines = []
            for b in boxes:
                if b.get("cls") not in cidx:
                    continue
                cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
                lines.append(f"{cidx[b['cls']]} {cx:.6f} {cy:.6f} {b['w']:.6f} {b['h']:.6f}")
            (lbld / (Path(p).stem + ".txt")).write_text("\n".join(lines), encoding="utf-8")
    data = {"path": str(ds_dir.resolve()), "train": "images/train", "val": "images/val",
            "names": {i: c for i, c in enumerate(classes)}}
    yp = ds_dir / "data.yaml"
    yp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return yp, (len(train), len(val))
