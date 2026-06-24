"""ScopeLab — 偵測（det）推論引擎（ultralytics YOLO）。

載入訓練輸出的 YOLO best.pt，對影像跑偵測，回傳「已畫框的影像（RGB）+ 每框類別/信心」。
ultralytics 重 → 在函式內惰性 import。
"""
from __future__ import annotations

import numpy as np


def predict_det(weights_path: str, paths: list[str], device_str: str, conf: float = 0.25) -> list[dict]:
    """對每張影像回 {path, image(RGB ndarray, 已畫框), dets:[(類別, 信心)...]}。"""
    from ultralytics import YOLO

    model = YOLO(weights_path)
    dev = 0 if device_str == "cuda" else "cpu"
    results = model.predict(paths, device=dev, conf=conf, verbose=False)
    out = []
    for p, r in zip(paths, results):
        rgb = np.ascontiguousarray(r.plot()[:, :, ::-1])     # ultralytics 回 BGR → 轉 RGB
        dets = [(r.names[int(b.cls[0])], float(b.conf[0])) for b in r.boxes]
        dets.sort(key=lambda d: d[1], reverse=True)
        out.append({"path": p, "image": rgb, "dets": dets})
    return out
