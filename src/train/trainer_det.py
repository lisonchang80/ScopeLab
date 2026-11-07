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
import shutil
import threading
from datetime import datetime
from pathlib import Path

from .dataset import build_det_dataset
from .device import gpu_stats, health_warnings
from .recipes import DET_RECIPES


# 建資料集搬到 dataset.py（純檔案處理、不 import torch，可單獨測；審查 SL-5）
_build_dataset = build_det_dataset


def train_det(name, files, labels, classes, recipe_key, device_info, out_dir,
              should_stop=None, epochs_override=None, edits=None):
    """edits：labels.json 的影像編輯對映（原圖 → 編輯版）。框是相對編輯後影像存的，
    所以資料集要複製編輯版（審查 SL-5）。"""
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
    yp, split = build_det_dataset(files, labels, classes, out / "dataset", edits)
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
