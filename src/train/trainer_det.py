"""ScopeLab — 偵測（det）訓練引擎（ultralytics YOLO）。

`train_det(...)` 是 generator，介面與 trainer_cls 對齊（同一個訓練分頁可驅動兩者）：
從「標註」分頁的 det labels 建 YOLO 資料集（含 train/val split）→ 背景 thread 跑
`YOLO.train`，`on_fit_epoch_end` callback 把 mAP 推進 queue → 主執行緒逐 epoch yield。

YOLO 的 best.pt 由 ultralytics 自動存（best fitness），訓練後複製成 runs/<ts>/best.pt，
與 cls 一致；另寫 metrics.json / model_card.json(task=det)。
ultralytics 重、且只在訓練時需要 → 在函式內惰性 import。
"""
from __future__ import annotations

import json
import queue
import random
import shutil
import threading
from datetime import datetime
from pathlib import Path

import yaml

from .device import gpu_stats, health_warnings
from .recipes import DET_RECIPES


def _build_dataset(files, labels, classes, ds_dir: Path):
    """把 det labels 寫成 YOLO 資料集；回 (data.yaml 路徑, (n_train, n_val))，不足回 (None, None)。"""
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
            shutil.copy2(p, imgd / Path(p).name)
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


def train_det(name, files, labels, classes, recipe_key, device_info, out_dir,
              should_stop=None, epochs_override=None):
    rc = DET_RECIPES[recipe_key]
    imgsz, model_name = rc["imgsz"], rc["model"]
    epochs = epochs_override or rc["epochs"]
    dev = 0 if device_info["device"] == "cuda" else "cpu"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    yield {"phase": "setup", "step": "建立 YOLO 資料集…"}
    if len(classes) < 1:
        yield {"phase": "error", "msg": "此專案沒有定義類別。"}
        return
    yp, split = _build_dataset(files, labels, classes, out / "dataset")
    if yp is None:
        yield {"phase": "error", "msg": "可用偵測標註不足（至少 2 張含框影像）。"}
        return
    n_train, n_val = split
    yield {"phase": "data", "n_train": n_train, "n_val": n_val, "n_classes": len(classes)}

    from ultralytics import YOLO
    try:
        model = YOLO(model_name + ".pt")
        used = model_name
    except Exception:                       # 離線且未快取 → 回退本機已有的 yolov8s
        model = YOLO("yolov8s.pt")
        used = "yolov8s(fallback)"
    yield {"phase": "model", "arch": used, "pretrained": True, "batch": "auto", "device": device_info}

    q: queue.Queue = queue.Queue()
    err = {}

    def on_fit_epoch_end(trainer):
        try:
            ep = int(getattr(trainer, "epoch", 0)) + 1
            m = trainer.metrics or {}
            q.put({"epoch": ep,
                   "map50": round(float(m.get("metrics/mAP50(B)", 0.0) or 0.0), 4),
                   "map": round(float(m.get("metrics/mAP50-95(B)", 0.0) or 0.0), 4)})
        except Exception:
            pass

    def run():
        try:
            model.add_callback("on_fit_epoch_end", on_fit_epoch_end)
            model.train(data=str(yp), epochs=epochs, imgsz=imgsz, batch=-1, device=dev,
                        project=str(out), name="yolo", exist_ok=True, verbose=False,
                        workers=0, plots=False)
        except Exception as e:
            err["e"] = str(e)
        finally:
            q.put({"_done": True})

    threading.Thread(target=run, daemon=True).start()

    best_map = -1.0
    while True:
        ev = q.get()
        if ev.get("_done"):
            break
        if ev["epoch"] > epochs:        # 訓練後 ultralytics 會對 best.pt 再驗一次 → 跳過多出的事件
            continue
        improved = ev["map50"] > best_map
        if improved:
            best_map = ev["map50"]
        stats = gpu_stats()
        yield {"phase": "epoch", "epoch": ev["epoch"], "total": epochs,
               "map50": ev["map50"], "map5095": ev["map"], "improved": improved,
               "best_map50": round(max(best_map, 0.0), 4),
               "gpu": stats, "warnings": health_warnings(stats)}

    if err:
        yield {"phase": "error", "msg": f"YOLO 訓練失敗：{err['e']}"}
        return

    yolo_best = out / "yolo" / "weights" / "best.pt"
    if yolo_best.is_file():
        shutil.copy2(yolo_best, out / "best.pt")
    bm = round(max(best_map, 0.0), 4)
    metrics = {"best_map50": bm, "n_train": n_train, "n_val": n_val, "classes": classes,
               "epochs": epochs, "model": used, "imgsz": imgsz}
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    card = {"name": name, "task": "det", "engine": "ultralytics-yolo", "arch": used,
            "classes": classes, "imgsz": imgsz, "recipe": recipe_key, "best_map50": bm,
            "n_train": n_train, "n_val": n_val, "device": device_info,
            "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    (out / "model_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    yield {"phase": "done", "summary": metrics, "out_dir": str(out),
           "weights": str(out / "best.pt"), "model_card": str(out / "model_card.json")}
