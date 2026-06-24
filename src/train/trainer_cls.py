"""ScopeLab — 分類（cls）訓練引擎。

`train_cls(...)` 是一個 **generator**：逐 epoch `yield` 事件 dict（setup/data/model/
epoch/done/error/stopped），呼叫端（UI 或 headless 測試）據此更新進度條與 checkpoint 打勾。
直接吃「標註」分頁的 labels（image_path → 類別名），免先匯出。

torch 等重套件在此模組頂層 import；本模組只在訓練 handler 內被惰性 import，
不影響 app 啟動速度。
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime
from pathlib import Path

import torch
import torch.nn as nn
import torchvision.transforms as T
from PIL import Image
from sklearn.metrics import f1_score
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader, Dataset
from torchvision import models

from .device import batch_for, gpu_stats, health_warnings
from .recipes import RECIPES

_MEAN = [0.485, 0.456, 0.406]
_STD = [0.229, 0.224, 0.225]


class _DS(Dataset):
    def __init__(self, items, tf):
        self.items, self.tf = items, tf

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        path, y = self.items[i]
        img = Image.open(path).convert("RGB")
        return self.tf(img), y


def _build_model(arch: str, n: int, pretrained: bool):
    """回 (model, 是否真的用了預訓練)。預訓練下載失敗（離線且未快取）自動退回從頭訓。"""
    fn = getattr(models, arch)
    model, used = None, False
    if pretrained:
        wname = {"resnet18": "ResNet18_Weights", "resnet34": "ResNet34_Weights",
                 "resnet50": "ResNet50_Weights"}.get(arch)
        try:
            weights = getattr(models, wname).DEFAULT if wname else None
            model = fn(weights=weights)
            used = weights is not None
        except Exception:
            model = None
    if model is None:
        model = fn(weights=None)
        used = False
    model.fc = nn.Linear(model.fc.in_features, n)
    return model, used


def train_cls(name, files, labels, classes, recipe_key, device_info, out_dir, should_stop=None):
    """逐 epoch yield 事件 dict 的訓練 generator。"""
    rc = RECIPES[recipe_key]
    img, epochs, lr, arch, pretrained = rc["img"], rc["epochs"], rc["lr"], rc["arch"], rc["pretrained"]
    dev = torch.device(device_info["device"])

    items = [(p, classes.index(labels[p])) for p in files
             if isinstance(labels.get(p), str) and labels.get(p) in classes]
    yield {"phase": "setup", "step": f"載入標註：{len(items)} 張可用"}
    if len(items) < 2 or len(classes) < 2:
        yield {"phase": "error", "msg": "可用標註不足（至少 2 類、≥2 張已標影像）。"}
        return

    ys = [y for _, y in items]
    try:
        tr, va = train_test_split(items, test_size=0.2, random_state=0, stratify=ys)
        if not va:
            raise ValueError
    except Exception:                       # 類別太少/太不均 → 退回隨機切
        shuffled = items[:]
        random.Random(0).shuffle(shuffled)
        k = max(1, int(len(shuffled) * 0.2))
        va, tr = shuffled[:k], (shuffled[k:] or shuffled[:k])
    yield {"phase": "data", "n_train": len(tr), "n_val": len(va), "n_classes": len(classes)}

    strong = recipe_key == "accurate"
    train_tf = T.Compose([
        T.Resize((img, img)), T.RandomHorizontalFlip(),
        *([T.RandomRotation(15), T.ColorJitter(0.2, 0.2, 0.2)] if strong else []),
        T.ToTensor(), T.Normalize(_MEAN, _STD)])
    val_tf = T.Compose([T.Resize((img, img)), T.ToTensor(), T.Normalize(_MEAN, _STD)])

    bs = batch_for(device_info.get("vram_gb", 0), img, arch)
    pin = dev.type == "cuda"
    train_dl = DataLoader(_DS(tr, train_tf), batch_size=bs, shuffle=True, num_workers=0, pin_memory=pin)
    val_dl = DataLoader(_DS(va, val_tf), batch_size=bs, shuffle=False, num_workers=0)

    model, used_pre = _build_model(arch, len(classes), pretrained)
    model.to(dev)
    yield {"phase": "model", "arch": arch, "pretrained": used_pre, "batch": bs,
           "device": device_info}

    opt = torch.optim.AdamW(model.parameters(), lr=lr)
    crit = nn.CrossEntropyLoss()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    best_acc, best_path = -1.0, out / "best.pt"

    for ep in range(1, epochs + 1):
        if should_stop and should_stop():
            yield {"phase": "stopped", "epoch": ep}
            return
        model.train()
        tot, nb = 0.0, 0
        for x, y in train_dl:
            x, y = x.to(dev), y.to(dev)
            opt.zero_grad()
            loss = crit(model(x), y)
            loss.backward()
            opt.step()
            tot += float(loss)
            nb += 1
        train_loss = tot / max(1, nb)
        if math.isnan(train_loss):
            yield {"phase": "error", "msg": "loss 變 NaN，已中止（試試降低學習率或換配方）。"}
            return

        model.eval()
        preds, gts = [], []
        with torch.no_grad():
            for x, y in val_dl:
                preds += model(x.to(dev)).argmax(1).cpu().tolist()
                gts += list(y.tolist())
        acc = sum(int(a == b) for a, b in zip(preds, gts)) / max(1, len(gts))
        f1 = f1_score(gts, preds, average="macro", zero_division=0)
        improved = acc > best_acc
        if improved:
            best_acc = acc
            torch.save({"state_dict": model.state_dict(), "arch": arch,
                        "classes": classes, "img": img}, best_path)
        stats = gpu_stats()
        yield {"phase": "epoch", "epoch": ep, "total": epochs,
               "train_loss": round(train_loss, 4), "val_acc": round(acc, 4),
               "val_f1": round(f1, 4), "improved": improved, "best_acc": round(best_acc, 4),
               "gpu": stats, "warnings": health_warnings(stats)}

    metrics = {"best_val_acc": round(best_acc, 4), "n_train": len(tr), "n_val": len(va),
               "classes": classes, "epochs": epochs, "arch": arch, "img_size": img}
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    card = {"name": name, "task": "cls", "engine": "torchvision-resnet", "arch": arch,
            "pretrained": used_pre, "classes": classes, "img_size": img, "recipe": recipe_key,
            "best_val_acc": round(best_acc, 4), "n_train": len(tr), "n_val": len(va),
            "device": device_info, "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    (out / "model_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    yield {"phase": "done", "summary": metrics, "out_dir": str(out),
           "weights": str(best_path), "model_card": str(out / "model_card.json")}
