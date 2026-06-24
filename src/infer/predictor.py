"""ScopeLab — 分類（cls）推論引擎。

載入「訓練」分頁產出的 `best.pt`（內含 state_dict / arch / classes / img），
重建 torchvision 模型跑預測。transforms 與訓練時的 val 一致。

torch 在此模組頂層 import，但只在推論 handler 內被惰性 import，不影響 app 啟動。
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torchvision.transforms as T
from PIL import Image
from torchvision import models

_MEAN = [0.485, 0.456, 0.406]
_STD = [0.229, 0.224, 0.225]


def load_model(weights_path: str, device):
    """回 (model, classes, img_size)。讀自家訓練輸出 → weights_only=False（信任本機檔）。"""
    ckpt = torch.load(weights_path, map_location=device, weights_only=False)
    arch, classes, img = ckpt["arch"], ckpt["classes"], ckpt["img"]
    model = getattr(models, arch)(weights=None)
    model.fc = nn.Linear(model.fc.in_features, len(classes))
    model.load_state_dict(ckpt["state_dict"])
    model.eval().to(device)
    return model, classes, img


def predict(model, classes, img: int, paths: list[str], device, topk: int = 3) -> list[dict]:
    """對每張影像回 {path, ok, top:[(類別, 機率)...]}（讀不到的影像 ok=False）。"""
    tf = T.Compose([T.Resize((img, img)), T.ToTensor(), T.Normalize(_MEAN, _STD)])
    k = min(topk, len(classes))
    out = []
    with torch.no_grad():
        for p in paths:
            try:
                x = tf(Image.open(p).convert("RGB")).unsqueeze(0).to(device)
            except Exception as e:
                out.append({"path": p, "ok": False, "err": str(e), "top": []})
                continue
            prob = torch.softmax(model(x), 1)[0].cpu()
            t = prob.topk(k)
            top = [(classes[i], float(prob[i])) for i in t.indices.tolist()]
            out.append({"path": p, "ok": True, "top": top})
    return out
