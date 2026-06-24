"""ScopeLab — 推論分頁（佔位骨架）。

label → train → **infer** 的末段：載入「訓練」分頁產出的模型，對新影像跑預測。
實作見 Phase 4（沿用 ClinScope / MatScope 推論引擎）。
"""
from __future__ import annotations

import gradio as gr


def build_infer_tab():
    """建立「推論」分頁內容（目前為規劃佔位，沿用 ScopeLab 既有 CSS class）。"""
    with gr.Column(elem_classes="ls-panel"):
        gr.HTML('<div class="ls-modal-title">🔍 推論　INFER</div>')
        gr.Markdown(
            "載入「**訓練**」分頁產出的模型，對新影像跑預測——閉合 label → train → infer。\n\n"
            "**即將支援（Phase 4）**\n"
            "- 選擇本專案 `runs/` 下的權重（或匯入外部 `.pt`）\n"
            "- 拖入 / 選資料夾批次推論；顯示類別機率（cls）或偵測框（det）\n"
            "- 匯出預測結果（CSV / 疊框圖）\n"
            "- 推論引擎與 ClinScope / MatScope 一致",
            elem_classes="infobar",
        )
        gr.Button("▶ 開始推論（Phase 4 啟用）", interactive=False, elem_classes="tbtn accent")
