"""ScopeLab — 訓練預設配方（cls + det）。

把「使用者只想選快/準」翻成具體超參數。三個等級 fast / balanced / accurate 對兩種任務：
  cls → torchvision ResNet
  det → ultralytics YOLO（取不到指定權重時 trainer 會回退 yolov8s，本機已快取＝離線可用）
"""
from __future__ import annotations

CLS_RECIPES = {
    "fast": {
        "label": "快速", "arch": "resnet18", "img": 160, "epochs": 8,
        "pretrained": False, "lr": 1e-3,
        "desc": "resnet18 從頭訓 · 160px · 8ep（最快跑通，不需下載權重）",
    },
    "balanced": {
        "label": "平衡", "arch": "resnet50", "img": 224, "epochs": 15,
        "pretrained": True, "lr": 3e-4,
        "desc": "resnet50 預訓練微調 · 224px · 15ep（速度與精度平衡）",
    },
    "accurate": {
        "label": "高精度", "arch": "resnet50", "img": 224, "epochs": 30,
        "pretrained": True, "lr": 2e-4,
        "desc": "resnet50 預訓練微調 · 224px · 30ep + 較強增強（最佳精度，較慢）",
    },
}

DET_RECIPES = {
    "fast": {
        "label": "快速", "model": "yolov8n", "imgsz": 480, "epochs": 40,
        "desc": "yolov8n · 480px · 40ep（最快跑通）",
    },
    "balanced": {
        "label": "平衡", "model": "yolov8s", "imgsz": 640, "epochs": 60,
        "desc": "yolov8s · 640px · 60ep（速度與精度平衡，權重已快取）",
    },
    "accurate": {
        "label": "高精度", "model": "yolov8m", "imgsz": 640, "epochs": 100,
        "desc": "yolov8m · 640px · 100ep（最佳精度，較慢）",
    },
}

# 向後相容：trainer_cls 仍用 RECIPES
RECIPES = CLS_RECIPES
DEFAULT_RECIPE = "balanced"


def recipe_choices(task: str = "cls"):
    """給 Gradio Radio 用的 (顯示文字, key) 清單，依任務給對應配方。"""
    src = DET_RECIPES if task == "det" else CLS_RECIPES
    return [(f"{v['label']}　— {v['desc']}", k) for k, v in src.items()]


def get_recipe(task: str, key: str) -> dict:
    return (DET_RECIPES if task == "det" else CLS_RECIPES)[key]
