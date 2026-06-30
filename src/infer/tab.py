"""ScopeLab — 推論分頁 UI（cls + det，三視窗外觀比照 ClinScope）。

版面同 ClinScope 推論工作台：
  上排  模型 MODEL（專案＋訓練結果 run 同一列 + 識別卡）｜顯示 DISPLAY（預覽選取的結果圖）
  下排  結果 RESULT（📁 選資料夾 → ▶ 批次推論 → 縮圖牆 + .ls-dt 明細表，點縮圖即在上方預覽）
依 run 的 model_card.task 分派：cls → 機率；det → 已畫框影像。
影像來源比照 ClinScope 與本專案「標記」分頁：原生資料夾選取器（非拖拉上傳框）。
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


def _pick_dir():              # 原生資料夾選取器（同 app._native_dir，避免循環 import 複製一份）
    if store.PUBLIC:          # 公開展示模式：不在伺服器端開原生對話框
        return ""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title="選擇推論影像資料夾")
        try:
            root.destroy()
        except Exception:
            pass
        return path or ""
    except Exception:
        return ""


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


_EMPTY = ('<div class="ls-empty-state"><div class="es-ic">🔍</div>'
          '<div class="es-t">尚無推論結果</div>'
          '<div class="es-s">在上方<b>選專案與訓練結果（run）</b>，按下方 '
          '<b>📁 選擇資料夾</b> 載入影像，再按 <b>▶ 批次推論</b>。</div></div>')


def _cls_table(res, thr=0.0):
    head = "<tr><th>影像</th><th>預測</th><th>信心</th><th>Top-3</th></tr>"
    rows = []
    for i, r in enumerate(res):
        name = html.escape(Path(r["path"]).name)
        if not r.get("ok"):
            rows.append(f'<tr class="ls-row" data-i="{i}"><td class="ls-name">{name}</td>'
                        f'<td colspan="3" class="infer-bad">讀取失敗</td></tr>')
            continue
        pred, conf = r["top"][0]
        pred_html = ('<b class="infer-bad">Null（待確認）</b>' if conf < thr   # 低於門檻 → 歸 Null
                     else f'<b>{html.escape(pred)}</b>')
        t3 = "　".join(f"{html.escape(c)} {p * 100:.0f}%" for c, p in r["top"])
        rows.append(f'<tr class="ls-row" data-i="{i}"><td class="ls-name" title="{name}">{name}</td>'
                    f'<td>{pred_html}</td><td>{conf * 100:.1f}%</td>'
                    f'<td class="infer-top">{t3}</td></tr>')
    return (f'<div class="ls-dt-wrap"><table class="ls-dt"><thead>{head}</thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _det_table(res):
    head = "<tr><th>影像</th><th>框數</th><th>偵測（前幾項）</th></tr>"
    rows = []
    for i, r in enumerate(res):
        name = html.escape(Path(r["path"]).name)
        if not r["dets"]:
            rows.append(f'<tr class="ls-row" data-i="{i}"><td class="ls-name" title="{name}">{name}</td>'
                        f'<td>0</td><td class="infer-top">（無偵測）</td></tr>')
            continue
        tops = "　".join(f"{html.escape(n)} {c * 100:.0f}%" for n, c in r["dets"][:6])
        rows.append(f'<tr class="ls-row" data-i="{i}"><td class="ls-name" title="{name}">{name}</td>'
                    f'<td><b>{len(r["dets"])}</b></td><td class="infer-top">{tops}</td></tr>')
    return (f'<div class="ls-dt-wrap"><table class="ls-dt det"><thead>{head}</thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _cls_meta(r, thr=0.0):
    if not r.get("ok"):
        return "讀取失敗"
    prefix = "**Null（待確認）** ← " if r["top"][0][1] < thr else ""
    return prefix + "　".join(f"**{html.escape(c)}** {p * 100:.0f}%" for c, p in r["top"])


def _det_meta(r):
    if not r["dets"]:
        return "（無偵測）"
    return f"{len(r['dets'])} 框：" + "　".join(f"{html.escape(n)} {c * 100:.0f}%" for n, c in r["dets"][:8])


def build_infer_tab():
    res_state = gr.State([])       # 預覽用：[{img, meta}]（推論前=原圖、推論後=結果）
    picked = gr.State([])          # 已選資料夾的影像路徑清單

    with gr.Row(equal_height=False, elem_classes="toprow"):
        with gr.Column(scale=5, elem_classes="ls-panel"):
            gr.HTML(_phead("模型", "MODEL", "選專案與訓練結果（run）"))
            with gr.Row(elem_classes="inforow"):
                proj_dd = gr.Dropdown(_all_projects(), value=None, label="專案（分類 / 偵測）",
                                      filterable=False, scale=3)
                run_dd = gr.Dropdown([], label="訓練結果（run）", filterable=False, scale=4)
                refresh_btn = gr.Button("🔄", elem_classes="tbtn", scale=0, min_width=46)
            card_md = gr.Markdown("", elem_classes="infobar")
            thr = gr.Slider(0.0, 1.0, value=0.5, step=0.01, label="信心門檻（分類）",
                            info="Top-1 機率低於此值 → 歸入 Null 待確認", elem_classes="thr")
        with gr.Column(scale=6, elem_classes="ls-panel"):
            gr.HTML(_phead("顯示", "DISPLAY", "點下方結果縮圖即在此預覽"))
            preview_img = gr.Image(show_label=False, height=520, elem_classes="preview")
            preview_meta = gr.Markdown("", visible=False, elem_classes="pmeta")

    with gr.Row(elem_classes="editrow"):
        with gr.Column(elem_classes="ls-panel"):
            gr.HTML(_phead("✎ 編輯", "EDIT", "選資料夾 → 批次推論 → 逐張結果"))
            with gr.Row(elem_classes="toolbar"):
                pick_btn = gr.Button("📁 選擇資料夾", elem_classes="tbtn", scale=0)
                infer_btn = gr.Button("▶ 批次推論", elem_classes="tbtn primary", scale=0)
            count_md = gr.Markdown("", visible=False, elem_classes="countbar")
            result_gallery = gr.Gallery(show_label=False, columns=8, object_fit="cover",
                                        allow_preview=False, elem_classes="grid")
            result_html = gr.HTML(_EMPTY, elem_classes="detail")

    # ---- 事件 ----
    def on_proj_change(slug):
        runs = _runs(slug)
        val = runs[0][1] if runs else None
        return gr.update(choices=runs, value=val), (_card(val) if val else "")

    def on_run_change(weights):
        return _card(weights) if weights else ""

    def on_refresh(slug):
        return gr.update(choices=_all_projects()), gr.update(choices=_runs(slug))

    def on_pick():
        if store.PUBLIC:
            gr.Warning(store.PUBLIC_MSG)
            return gr.update(), gr.update(), gr.update(), gr.update()
        path = _pick_dir()
        if not path:
            return gr.update(), gr.update(), gr.update(), gr.update()
        files = store.scan_folder(path)
        n = len(files)
        gallery = [(p, Path(p).name) for p in files]
        items = [{"img": p, "meta": "（尚未推論）"} for p in files]
        head = (f"📂 已載入 {n} 張 · 按 ▶ 批次推論" if n
                else "⚠️ 此資料夾沒有支援的影像檔")
        return (files, gr.update(value=head, visible=True),
                gr.update(value=gallery), items)

    def on_infer(weights, plist, thr, progress=gr.Progress()):
        if not weights:
            raise gr.Error("請先選一個訓練結果（run）；若沒有，先到「訓練」分頁訓一個。")
        if not plist:
            raise gr.Error("請先按「📁 選擇資料夾」載入影像。")
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
            items = [{"img": r["path"], "meta": _cls_meta(r, thr)} for r in res]
            ok = sum(1 for r in res if r.get("ok"))
            low = sum(1 for r in res if r.get("ok") and r["top"][0][1] < thr)
            head = f"✅ 推論完成：{ok}/{len(res)} 張" + (f" · {low} 張低於門檻歸 Null" if low else "")
            table = _cls_table(res, thr)
        return (gr.update(value=head, visible=True), gr.update(value=gallery), table, items)

    def on_select(items, evt: gr.SelectData):
        i = evt.index if evt is not None else None
        if not items or i is None or i >= len(items):
            return gr.update(), gr.update()
        it = items[i]
        return gr.update(value=it["img"]), gr.update(value=it["meta"], visible=True)

    refresh_btn.click(on_refresh, [proj_dd], [proj_dd, run_dd])
    proj_dd.change(on_proj_change, [proj_dd], [run_dd, card_md])
    run_dd.change(on_run_change, [run_dd], [card_md])
    pick_btn.click(on_pick, None, [picked, count_md, result_gallery, res_state])
    infer_btn.click(on_infer, [run_dd, picked, thr], [count_md, result_gallery, result_html, res_state])
    result_gallery.select(on_select, [res_state], [preview_img, preview_meta])

    # 回傳供「訓練」分頁『用此模型推論』跨頁帶入專案/run
    return proj_dd, run_dd
