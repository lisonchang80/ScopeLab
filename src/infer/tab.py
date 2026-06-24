"""ScopeLab — 推論分頁 UI（cls）。

選分類專案 → 選一個訓練結果 run（best.pt）→ 拖入影像 → ▶ 推論。
torch / 推論引擎只在 handler 內惰性 import；store 輕量、建頁時 import。
"""
from __future__ import annotations

import html
import json
from pathlib import Path

import gradio as gr

from .. import store


def _cls_projects():
    return [(f"{c['name']}（{len(c.get('classes', []))} 類）", c["slug"])
            for c in store.list_projects() if c.get("task") == "cls"]


def _runs(slug):
    """列出該專案 runs/ 下含 best.pt 的訓練結果，標籤帶 acc/arch（讀 model_card）。"""
    if not slug:
        return []
    d = store.project_dir(slug) / "runs"
    if not d.is_dir():
        return []
    out = []
    for r in sorted([x for x in d.iterdir() if x.is_dir()], reverse=True):
        w = r / "best.pt"
        if not w.is_file():
            continue
        tag = ""
        mc = r / "model_card.json"
        if mc.is_file():
            try:
                c = json.loads(mc.read_text(encoding="utf-8"))
                tag = f" · acc {c.get('best_val_acc', '?')} · {c.get('arch', '')}"
            except Exception:
                pass
        out.append((f"{r.name}{tag}", str(w)))
    return out


def _result_table(res: list[dict]) -> str:
    rows = []
    for r in res:
        name = html.escape(Path(r["path"]).name)
        if not r.get("ok"):
            rows.append(f"<tr><td>{name}</td><td colspan='3' class='infer-bad'>讀取失敗</td></tr>")
            continue
        top = r["top"]
        pred, conf = top[0]
        t3 = "　".join(f"{html.escape(c)} {p * 100:.0f}%" for c, p in top)
        rows.append(f"<tr><td>{name}</td><td><b>{html.escape(pred)}</b></td>"
                    f"<td>{conf * 100:.1f}%</td><td class='infer-top'>{t3}</td></tr>")
    return ("<table class='infer-tbl'><thead><tr><th>影像</th><th>預測</th>"
            "<th>信心</th><th>Top-3</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def build_infer_tab():
    with gr.Column(elem_classes="ls-panel"):
        gr.HTML('<div class="ls-modal-title">🔍 推論　INFER</div>')
        with gr.Row(elem_classes="inforow"):
            proj_dd = gr.Dropdown(_cls_projects(), label="分類專案（cls）",
                                  filterable=False, scale=3)
            run_dd = gr.Dropdown([], label="訓練結果（run）", filterable=False, scale=4)
            refresh_btn = gr.Button("🔄", elem_classes="tbtn", scale=0, min_width=48)
        files = gr.File(file_count="multiple", type="filepath", file_types=["image"],
                        label="拖入 / 選擇要預測的影像（可多張）")
        infer_btn = gr.Button("▶ 推論", elem_classes="tbtn accent")
        result_html = gr.HTML("")

    # ---- 事件 ----
    def on_proj_change(slug):
        runs = _runs(slug)
        return gr.update(choices=runs, value=(runs[0][1] if runs else None))

    def on_refresh(slug):
        return gr.update(choices=_cls_projects()), gr.update(choices=_runs(slug))

    def on_infer(weights, paths, progress=gr.Progress()):
        if not weights:
            raise gr.Error("請先選一個訓練結果（run）；若沒有，先到「訓練」分頁訓一個。")
        if not paths:
            raise gr.Error("請先拖入至少一張影像。")
        from . import predictor
        from ..train import device
        dstr = device.pick_device()["device"]
        progress(0.2, desc="載入模型…")
        model, classes, img = predictor.load_model(weights, dstr)
        progress(0.5, desc="推論中…")
        plist = paths if isinstance(paths, list) else [paths]
        res = predictor.predict(model, classes, img, plist, dstr)
        progress(1.0, desc="完成")
        ok = sum(1 for r in res if r.get("ok"))
        head = f"<div class='infobar'>✅ 推論完成：{ok}/{len(res)} 張</div>"
        return head + _result_table(res)

    refresh_btn.click(on_refresh, [proj_dd], [proj_dd, run_dd])
    proj_dd.change(on_proj_change, [proj_dd], [run_dd])
    infer_btn.click(on_infer, [run_dd, files], [result_html])
