"""ScopeLab — 訓練分頁（佔位骨架）。

label → **train** → infer 的中段：用「標註」分頁建好的資料集，在本機離線訓練模型，
全程純程式、不需 agent。實作見 Phase 3（cls 先：ResNet；det：YOLO）。
"""
from __future__ import annotations

import gradio as gr


def build_train_tab():
    """建立「訓練」分頁內容（目前為規劃佔位，沿用 ScopeLab 既有 CSS class）。"""
    with gr.Column(elem_classes="ls-panel"):
        gr.HTML('<div class="ls-modal-title">🏋 訓練　TRAIN</div>')
        gr.Markdown(
            "用「**標註**」分頁建好的資料集，在這台機器離線訓練模型——全程純程式、無需 agent。\n\n"
            "**即將支援（Phase 3）**\n"
            "- 自動偵測 GPU/CPU（`torch.cuda`）＋依 VRAM 查表調 batch / img-size，OOM 自動降載\n"
            "- 預設配方：快速 / 平衡 / 高精度（cls → ResNet、det → YOLO）\n"
            "- 即時進度條 + checkpoint 打勾清單（逐 epoch 存 best）\n"
            "- GPU 看門狗：util=0 / OOM / 過熱 / NaN-loss 自動告警與中止\n"
            "- 產出 `projects/<專案>/runs/<時間戳>/best.pt + metrics + model_card.json`，"
            "可直接註冊進 ClinScope / MatScope",
            elem_classes="infobar",
        )
        gr.Button("▶ 開始訓練（Phase 3 啟用）", interactive=False, elem_classes="tbtn accent")
