"""ScopeLab — 推論分頁 UI（cls + det，三視窗外觀比照 ClinScope）。

版面同 ClinScope 推論工作台：
  上排  模型 MODEL（專案 + 訓練結果 run + 識別卡）｜顯示 DISPLAY（預覽選取的結果圖）
  下排  結果 RESULT（拖入影像 → ▶ 批次推論 → 縮圖牆 + 明細表，點縮圖即在上方預覽）
依 run 的 model_card.task 分派：cls → 機率；det → 已畫框影像。
torch / 推論引擎只在 handler 內惰性 import；store 輕量、建頁時 import。
"""
from __future__ import annotations

import html
import json
from pathlib import Path

import gradio as gr

from .. import store


def _phead(tag, en, sub):     # 比照 app.panel_head（避免 infer→app 循環 import，這裡複製）
    return (f'<div class="phead"><div><span class="ph-tag">{tag}</span>'
            f'<span class="ph-en">{en}</span></div><span class="ph-sub">{sub}</span></div>')


def _all_projects():
    out = []
    for c in store.list_projects():
        tn = "偵測" if c.get("task") == "det" else "分類"
        out.append((f"{c['name']}（{tn}·{len(c.get('classes', []))} 類）", c["slug"]))
    return out


def _runs(slug):
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
                tag = (f" · mAP50 {c.get('best_map50', '?')}" if c.get("task") == "det"
                       else f" · acc {c.get('best_val_acc', '?')}") + f" · {c.get('arch', '')}"
            except Exception:
                pass
        out.append((f"{r.name}{tag}", str(w)))
    return out


def _card(weights):
    mc = Path(weights).parent / "model_card.json" if weights else None
    if not mc or not mc.is_file():
        return ""
    try:
        c = json.loads(mc.read_text(encoding="utf-8"))
    except Exception:
        return ""
    det = c.get("task") == "det"
    score = (f"mAP50 **{c.get('best_map50', '?')}**" if det else f"val acc **{c.get('best_val_acc', '?')}**")
    return (f"**{c.get('name', '')}** · {'偵測' if det else '分類'} · {score}  \n"
            f"引擎 {c.get('engine', '')} · {c.get('arch', '')}  \n"
            f"類別（{len(c.get('classes', []))}）：{'、'.join(c.get('classes', []))}  \n"
            f"訓練 {c.get('trained_at', '')}")


def _run_task(weights):
    mc = Path(weights).parent / "model_card.json" if weights else None
    if mc and mc.is_file():
        try:
            return json.loads(mc.read_text(encoding="utf-8")).get("task", "cls")
        except Exception:
            pass
    return "cls"


def _cls_table(res):
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
    return ("<table class='infer-tbl'><thead><tr><th>影像</th><th>預測</th><th>信心</th>"
            "<th>Top-3</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def _det_table(res):
    rows = []
    for r in res:
        name = html.escape(Path(r["path"]).name)
        if not r["dets"]:
            rows.append(f"<tr><td>{name}</td><td>0</td><td>（無偵測）</td></tr>")
            continue
        tops = "　".join(f"{html.escape(n)} {c * 100:.0f}%" for n, c in r["dets"][:6])
        rows.append(f"<tr><td>{name}</td><td><b>{len(r['dets'])}</b></td><td class='infer-top'>{tops}</td></tr>")
    return ("<table class='infer-tbl'><thead><tr><th>影像</th><th>框數</th>"
            "<th>偵測（前幾項）</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def _cls_meta(r):
    if not r.get("ok"):
        return "讀取失敗"
    return "　".join(f"**{html.escape(c)}** {p * 100:.0f}%" for c, p in r["top"])


def _det_meta(r):
    if not r["dets"]:
        return "（無偵測）"
    return f"{len(r['dets'])} 框：" + "　".join(f"{html.escape(n)} {c * 100:.0f}%" for n, c in r["dets"][:8])


def build_infer_tab():
    res_state = gr.State([])

    with gr.Row(equal_height=False, elem_classes="toprow"):
        with gr.Column(scale=5, elem_classes="ls-panel"):
            gr.HTML(_phead("模型", "MODEL", "選專案與訓練結果（run）"))
            with gr.Row(elem_classes="inforow"):
                proj_dd = gr.Dropdown(_all_projects(), value=None, label="專案（分類 / 偵測）",
                                      filterable=False, scale=3)
                refresh_btn = gr.Button("🔄", elem_classes="tbtn", scale=0, min_width=48)
            run_dd = gr.Dropdown([], label="訓練結果（run）", filterable=False)
            card_md = gr.Markdown("", elem_classes="infobar")
        with gr.Column(scale=6, elem_classes="ls-panel"):
            gr.HTML(_phead("顯示", "DISPLAY", "點下方結果縮圖即在此預覽"))
            preview_img = gr.Image(show_label=False, elem_classes="preview")
            preview_meta = gr.Markdown("批次推論後，點任一結果縮圖檢視。", elem_classes="pmeta")

    with gr.Row(elem_classes="editrow"):
        with gr.Column(elem_classes="ls-panel"):
            gr.HTML(_phead("結果", "RESULT", "拖入影像 → ▶ 批次推論 → 逐張結果"))
            with gr.Row(elem_classes="toolbar"):
                files = gr.File(file_count="multiple", type="filepath", file_types=["image"],
                                label="拖入 / 選擇影像（可多張）", scale=3)
                infer_btn = gr.Button("▶ 批次推論", elem_classes="tbtn primary", scale=0)
            count_md = gr.Markdown("", visible=False, elem_classes="countbar")
            result_gallery = gr.Gallery(show_label=False, columns=6, object_fit="cover",
                                        allow_preview=False, elem_classes="grid")
            result_html = gr.HTML("", elem_classes="detail")

    # ---- 事件 ----
    def on_proj_change(slug):
        runs = _runs(slug)
        val = runs[0][1] if runs else None
        return gr.update(choices=runs, value=val), (_card(val) if val else "")

    def on_run_change(weights):
        return _card(weights) if weights else ""

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
            gallery = [(r["image"], Path(r["path"]).name) for r in res]
            items = [{"img": r["image"], "meta": _det_meta(r)} for r in res]
            total = sum(len(r["dets"]) for r in res)
            head = f"✅ 偵測完成：{len(res)} 張 · 共 {total} 個框"
            table = _det_table(res)
        else:
            from . import predictor
            model, classes, img = predictor.load_model(weights, dstr)
            progress(0.6, desc="推論中…")
            res = predictor.predict(model, classes, img, plist, dstr)
            progress(1.0, desc="完成")
            gallery = [(r["path"], Path(r["path"]).name) for r in res]
            items = [{"img": r["path"], "meta": _cls_meta(r)} for r in res]
            ok = sum(1 for r in res if r.get("ok"))
            head = f"✅ 推論完成：{ok}/{len(res)} 張"
            table = _cls_table(res)
        return (gr.update(value=head, visible=True), gr.update(value=gallery), table, items)

    def on_select(items, evt: gr.SelectData):
        i = evt.index if evt is not None else None
        if not items or i is None or i >= len(items):
            return gr.update(), gr.update()
        it = items[i]
        return gr.update(value=it["img"]), it["meta"]

    refresh_btn.click(on_refresh, [proj_dd], [proj_dd, run_dd])
    proj_dd.change(on_proj_change, [proj_dd], [run_dd, card_md])
    run_dd.change(on_run_change, [run_dd], [card_md])
    infer_btn.click(on_infer, [run_dd, files], [count_md, result_gallery, result_html, res_state])
    result_gallery.select(on_select, [res_state], [preview_img, preview_meta])

    # 回傳供「訓練」分頁『用此模型推論』跨頁帶入專案/run
    return proj_dd, run_dd
