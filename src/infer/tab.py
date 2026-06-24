"""ScopeLab — 推論分頁 UI（cls + det 統一）。

選專案 → 選一個訓練結果 run（best.pt）→ 拖入影像 → ▶ 推論。
依 run 的 model_card.task 分派：cls → 機率表；det → 已畫框的影像圖庫 + 偵測摘要。
torch / 推論引擎只在 handler 內惰性 import；store 輕量、建頁時 import。
"""
from __future__ import annotations

import html
import json
from pathlib import Path

import gradio as gr

from .. import store


def _all_projects():
    out = []
    for c in store.list_projects():
        tn = "偵測" if c.get("task") == "det" else "分類"
        out.append((f"{c['name']}（{tn}·{len(c.get('classes', []))} 類）", c["slug"]))
    return out


def _runs(slug):
    """列出該專案 runs/ 下含 best.pt 的訓練結果，標籤帶 acc/mAP50 + arch。"""
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
                if c.get("task") == "det":
                    tag = f" · mAP50 {c.get('best_map50', '?')} · {c.get('arch', '')}"
                else:
                    tag = f" · acc {c.get('best_val_acc', '?')} · {c.get('arch', '')}"
            except Exception:
                pass
        out.append((f"{r.name}{tag}", str(w)))
    return out


def _run_task(weights_path: str) -> str:
    mc = Path(weights_path).parent / "model_card.json"
    if mc.is_file():
        try:
            return json.loads(mc.read_text(encoding="utf-8")).get("task", "cls")
        except Exception:
            pass
    return "cls"


def _cls_table(res: list[dict]) -> str:
    rows = []
    for r in res:
        name = html.escape(Path(r["path"]).name)
        if not r.get("ok"):
            rows.append(f"<tr><td>{name}</td><td colspan='3' class='infer-bad'>讀取失敗</td></tr>")
            continue
        pred, conf = r["top"][0]
        t3 = "　".join(f"{html.escape(c)} {p * 100:.0f}%" for c, p in r["top"])
        rows.append(f"<tr><td>{name}</td><td><b>{html.escape(pred)}</b></td>"
                    f"<td>{conf * 100:.1f}%</td><td class='infer-top'>{t3}</td></tr>")
    return ("<table class='infer-tbl'><thead><tr><th>影像</th><th>預測</th>"
            "<th>信心</th><th>Top-3</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def _det_caption(r: dict) -> str:
    name = Path(r["path"]).name
    if not r["dets"]:
        return f"{name}：（無偵測）"
    tops = "，".join(f"{n} {c * 100:.0f}%" for n, c in r["dets"][:5])
    return f"{name}：{len(r['dets'])} 框 · {tops}"


def build_infer_tab():
    with gr.Column(elem_classes="ls-panel"):
        gr.HTML('<div class="ls-modal-title">🔍 推論　INFER</div>')
        with gr.Row(elem_classes="inforow"):
            proj_dd = gr.Dropdown(_all_projects(), value=None, label="專案（分類 / 偵測）",
                                  filterable=False, scale=3)
            run_dd = gr.Dropdown([], label="訓練結果（run）", filterable=False, scale=4)
            refresh_btn = gr.Button("🔄", elem_classes="tbtn", scale=0, min_width=48)
        files = gr.File(file_count="multiple", type="filepath", file_types=["image"],
                        label="拖入 / 選擇要預測的影像（可多張）")
        infer_btn = gr.Button("▶ 推論", elem_classes="tbtn accent")
        result_html = gr.HTML("")
        result_gallery = gr.Gallery(visible=False, show_label=False, columns=3,
                                    object_fit="contain", elem_classes="grid")

    # ---- 事件 ----
    def on_proj_change(slug):
        runs = _runs(slug)
        return gr.update(choices=runs, value=(runs[0][1] if runs else None))

    def on_refresh(slug):
        return gr.update(choices=_all_projects()), gr.update(choices=_runs(slug))

    def on_infer(weights, paths, progress=gr.Progress()):
        if not weights:
            raise gr.Error("請先選一個訓練結果（run）；若沒有，先到「訓練」分頁訓一個。")
        if not paths:
            raise gr.Error("請先拖入至少一張影像。")
        plist = paths if isinstance(paths, list) else [paths]
        from ..train import device
        dstr = device.pick_device()["device"]
        task = _run_task(weights)
        progress(0.3, desc="載入模型…")
        if task == "det":
            from . import detector
            res = detector.predict_det(weights, plist, dstr)
            progress(1.0, desc="完成")
            total = sum(len(r["dets"]) for r in res)
            head = f"<div class='infobar'>✅ 偵測完成：{len(res)} 張 · 共 {total} 個框</div>"
            gallery = [(r["image"], _det_caption(r)) for r in res]
            return head, gr.update(value=gallery, visible=True)
        # cls
        from . import predictor
        model, classes, img = predictor.load_model(weights, dstr)
        progress(0.6, desc="推論中…")
        res = predictor.predict(model, classes, img, plist, dstr)
        progress(1.0, desc="完成")
        ok = sum(1 for r in res if r.get("ok"))
        head = f"<div class='infobar'>✅ 推論完成：{ok}/{len(res)} 張</div>"
        return head + _cls_table(res), gr.update(value=[], visible=False)

    refresh_btn.click(on_refresh, [proj_dd], [proj_dd, run_dd])
    proj_dd.change(on_proj_change, [proj_dd], [run_dd])
    infer_btn.click(on_infer, [run_dd, files], [result_html, result_gallery])
    # 回傳供「訓練」分頁『用此模型推論』跨頁帶入專案/run
    return proj_dd, run_dd
