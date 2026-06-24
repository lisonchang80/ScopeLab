"""ScopeLab — 訓練預設配方（cls）。

配方把「使用者只想選快/準」翻譯成具體超參數。det 之後另列。
- 快速 fast      resnet18 從頭訓（離線一定可跑，不需下載權重），小圖少 epoch，先跑通整條
- 平衡 balanced  resnet50 + ImageNet 預訓練（本機已快取 → 離線可用），224、中量 epoch
- 高精度 accurate resnet50 + 預訓練，224、多 epoch + 較強增強
"""
from __future__ import annotations

RECIPES = {
    "fast": {
        "label": "快速", "arch": "resnet18", "img": 160, "epochs": 8,
        "pretrained": False, "lr": 1e-3,
        "desc": "resnet18 從頭訓 · 160px · 8 epoch（最快跑通，不需下載權重）",
    },
    "balanced": {
        "label": "平衡", "arch": "resnet50", "img": 224, "epochs": 15,
        "pretrained": True, "lr": 3e-4,
        "desc": "resnet50 預訓練微調 · 224px · 15 epoch（速度與精度平衡）",
    },
    "accurate": {
        "label": "高精度", "arch": "resnet50", "img": 224, "epochs": 30,
        "pretrained": True, "lr": 2e-4,
        "desc": "resnet50 預訓練微調 · 224px · 30 epoch + 較強增強（最佳精度，較慢）",
    },
}

DEFAULT_RECIPE = "balanced"


def recipe_choices():
    """給 Gradio Radio 用的 (顯示文字, key) 清單。"""
    return [(f"{v['label']}　— {v['desc']}", k) for k, v in RECIPES.items()]
