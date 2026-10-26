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
import re
from pathlib import Path

import gradio as gr

from .. import store


def _phead(tag, en, sub):     # 比照 app.panel_head（避免 infer→app 循環 import，這裡複製）
    return (f'<div class="phead"><div><span class="ph-tag">{tag}</span>'
            f'<span class="ph-en">{en}</span></div><span class="ph-sub">{sub}</span></div>')


def _pick_dir():              # 原生資料夾選取器（同 app._native_dir，避免循環 import 複製一份）
    # 是否允許由呼叫端（on_pick → auth.public_locked）把關；站主開「本地模式」才走到這。
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


def _cls_table(res, thr=0.0, ov=None):
    ov = ov or {}
    head = "<tr><th>影像</th><th>預測</th><th>信心</th><th>Top-3</th></tr>"
    rows = []
    for i, r in enumerate(res):
        name = html.escape(Path(r["path"]).name)
        if not r.get("ok"):
            rows.append(f'<tr class="ls-row" data-i="{i}"><td class="ls-name">{name}</td>'
                        f'<td colspan="3" class="infer-bad">讀取失敗</td></tr>')
            continue
        pred, conf = r["top"][0]
        if i in ov:                                                  # 手動切換分類
            pred_html = f'<b class="infer-edit">{html.escape(ov[i])}</b>'
        elif conf < thr:                                             # 低於門檻 → 歸 Null
            pred_html = '<b class="infer-bad">Null（待確認）</b>'
        else:
            pred_html = f'<b>{html.escape(pred)}</b>'
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


def _cls_meta(r, thr=0.0, override=None):
    if not r.get("ok"):
        return "讀取失敗"
    if override:
        prefix = f"**{html.escape(override)}**（手動切換）← "
    elif r["top"][0][1] < thr:
        prefix = "**Null（待確認）** ← "
    else:
        prefix = ""
    return prefix + "　".join(f"**{html.escape(c)}** {p * 100:.0f}%" for c, p in r["top"])


def _det_meta(r):
    if not r["dets"]:
        return "（無偵測）"
    return f"{len(r['dets'])} 框：" + "　".join(f"{html.escape(n)} {c * 100:.0f}%" for n, c in r["dets"][:8])


SW_PLACEHOLDER = "⇄ 切換分類"


def _eff_class(r, thr, override):
    """這張影像最終判定的類別（給 CSV / 另存資料夾用）。手動切換 > 門檻歸 Null > Top-1。"""
    if not r.get("ok"):
        return "讀取失敗"
    if override:
        return override
    if r["top"][0][1] < thr:
        return "Null"
    return r["top"][0][0]


def _render(raw, thr):
    """由完整結果 raw + 門檻 thr 算出 (head, gallery, items, table)；批次/單張/切換/套用門檻共用。"""
    task = raw.get("task", "cls"); res = raw.get("res", []); ov = raw.get("ov", {})
    if task == "det":
        gallery = [(r["image"], Path(r["path"]).name) for r in res]
        items = [{"img": r["image"], "meta": _det_meta(r)} for r in res]
        total = sum(len(r["dets"]) for r in res)
        head = f"✅ 偵測完成：{len(res)} 張 · 共 {total} 個框"
        table = _det_table(res)
    else:
        gallery = [(r["path"], Path(r["path"]).name) for r in res]
        items = [{"img": r["path"], "meta": _cls_meta(r, thr, ov.get(i))} for i, r in enumerate(res)]
        ok = sum(1 for r in res if r.get("ok"))
        low = sum(1 for i, r in enumerate(res) if r.get("ok") and i not in ov and r["top"][0][1] < thr)
        head = f"✅ 推論完成：{ok}/{len(res)} 張" + (f" · {low} 張低於門檻歸 Null" if low else "")
        table = _cls_table(res, thr, ov)
    return head, gallery, items, table


def _safe_name(s):
    return re.sub(r'[\\/:*?"<>|]+', "_", str(s)).strip() or "未命名"


def _export_csv(raw, thr):
    """把目前結果（含手動切換 / 門檻歸 Null）寫成 CSV → exports/infer_results.csv。"""
    import csv as _csv
    task = raw.get("task", "cls"); res = raw.get("res", []); ov = raw.get("ov", {})
    out_dir = store.DATA_ROOT / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    fp = out_dir / "infer_results.csv"
    with open(fp, "w", newline="", encoding="utf-8-sig") as f:
        w = _csv.writer(f)
        if task == "det":
            w.writerow(["檔名", "框數", "偵測（前幾項）"])
            for r in res:
                tops = "; ".join(f"{n} {c * 100:.0f}%" for n, c in r["dets"][:8])
                w.writerow([Path(r["path"]).name, len(r["dets"]), tops])
        else:
            w.writerow(["檔名", "預測", "Top-1信心(%)", "Top-3", "手動切換"])
            for i, r in enumerate(res):
                if not r.get("ok"):
                    w.writerow([Path(r["path"]).name, "讀取失敗", "", "", ""])
                    continue
                t3 = "; ".join(f"{c} {p * 100:.0f}%" for c, p in r["top"])
                w.writerow([Path(r["path"]).name, _eff_class(r, thr, ov.get(i)),
                            f"{r['top'][0][1] * 100:.1f}", t3, "是" if i in ov else ""])
    return str(fp)


def _save_sorted(raw, thr):
    """把影像依最終判定類別複製到 exports/infer_sorted/<類別>/（偵測則依框數分夾）。"""
    import shutil
    task = raw.get("task", "cls"); res = raw.get("res", []); ov = raw.get("ov", {})
    dest = store.DATA_ROOT / "exports" / "infer_sorted"
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    n = 0
    for i, r in enumerate(res):
        if task == "cls" and not r.get("ok"):
            continue
        cls = _eff_class(r, thr, ov.get(i)) if task == "cls" else f"{len(r['dets'])}框"
        d = dest / _safe_name(cls)
        d.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(r["path"], d / Path(r["path"]).name)
            n += 1
        except Exception:
            pass
    return n, str(dest)


def build_infer_tab():
    res_state = gr.State([])       # 預覽用：[{img, meta}]（推論前=原圖、推論後=結果）
    picked = gr.State([])          # 已選資料夾的影像路徑清單
    raw_state = gr.State({})       # 完整結果：{task, res, classes, ov:{idx→手動類別}}
    sel_state = gr.State(None)     # 目前點選的縮圖索引（給 單張計算 / 切換分類）

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
            gr.HTML(_phead("🔍 推論", "INFER", "選資料夾 → 批次計算 → 逐張結果 · 切換分類 / 套用門檻 / 匯出"))
            with gr.Row(elem_classes="toolbar"):
                pick_btn = gr.Button("📁 選擇資料夾", elem_classes="tbtn", scale=0)
                view_dd = gr.Dropdown(["👁 詳細資料", "👁 圖示"], value="👁 詳細資料",
                                      show_label=False, container=False, elem_classes="shortdd")
                infer_btn = gr.Button("▶ 批次計算", elem_classes="tbtn primary", scale=0)
                single_btn = gr.Button("◉ 單張計算", elem_classes="tbtn navy2", scale=0)
                switch_dd = gr.Dropdown([SW_PLACEHOLDER], value=SW_PLACEHOLDER,
                                        show_label=False, container=False, elem_classes="shortdd")
                thr_btn = gr.Button("⚖ 套用門檻", elem_classes="tbtn", scale=0)
                csv_btn = gr.Button("↓ 匯出 CSV", elem_classes="tbtn", scale=0)
                save_btn = gr.Button("💾 另存資料夾", elem_classes="tbtn", scale=0)
            count_md = gr.Markdown("", visible=False, elem_classes="countbar")
            result_gallery = gr.Gallery(show_label=False, columns=8, object_fit="cover",
                                        allow_preview=False, elem_classes="grid")
            result_html = gr.HTML(_EMPTY, elem_classes="detail")
            csv_file = gr.File(label="下載 CSV", visible=False)

    # ---- 事件 ----
    def on_proj_change(slug):
        runs = _runs(slug)
        val = runs[0][1] if runs else None
        return gr.update(choices=runs, value=val), (_card(val) if val else "")

    def on_run_change(weights):
        return _card(weights) if weights else ""

    def on_refresh(slug):
        return gr.update(choices=_all_projects()), gr.update(choices=_runs(slug))

    def on_view(v):
        return gr.update(visible=(v == "👁 詳細資料"))

    def on_pick(request: gr.Request = None):
        from .. import auth
        if auth.public_locked(request):
            gr.Warning(auth.locked_msg(request))
            return gr.update(), gr.update(), gr.update(), gr.update()
        path = _pick_dir()
        if not path:
            return gr.update(), gr.update(), gr.update(), gr.update()
        files = store.scan_folder(path)
        n = len(files)
        gallery = [(p, Path(p).name) for p in files]
        items = [{"img": p, "meta": "（尚未推論）"} for p in files]
        head = (f"📂 已載入 {n} 張 · 按 ▶ 批次計算" if n
                else "⚠️ 此資料夾沒有支援的影像檔")
        return (files, gr.update(value=head, visible=True),
                gr.update(value=gallery), items)

    def _infer_all(weights, plist):
        """跑完整推論，回標準化 raw：{task, res, classes, ov}。批次/單張共用。"""
        from ..train import device
        dstr = device.pick_device()["device"]
        task = _run_task(weights)
        if task == "det":
            from . import detector
            res = detector.predict_det(weights, plist, dstr)
            classes = []
        else:
            from . import predictor
            model, classes, img = predictor.load_model(weights, dstr)
            res = predictor.predict(model, classes, img, plist, dstr)
        return {"task": task, "res": res, "classes": list(classes), "ov": {}}

    def on_infer(weights, plist, thr, request: gr.Request = None, progress=gr.Progress()):
        from .. import auth
        if auth.public_locked(request):     # 批次推論吃本機 GPU：訪客／預覽中一律擋
            gr.Warning(auth.locked_msg(request))
            return (gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update())
        if not weights:
            raise gr.Error("請先選一個訓練結果（run）；若沒有，先到「訓練」分頁訓一個。")
        if not plist:
            raise gr.Error("請先按「📁 選擇資料夾」載入影像。")
        progress(0.4, desc="載入模型 / 推論中…")
        raw = _infer_all(weights, plist)
        progress(1.0, desc="完成")
        head, gallery, items, table = _render(raw, thr)
        sw = gr.update(choices=[SW_PLACEHOLDER] + raw["classes"], value=SW_PLACEHOLDER,
                       interactive=bool(raw["classes"]))
        return (gr.update(value=head, visible=True), gr.update(value=gallery),
                table, items, raw, sw)

    def on_single(weights, raw, sel, thr, request: gr.Request = None):
        from .. import auth
        if auth.public_locked(request):
            gr.Warning(auth.locked_msg(request))
            return gr.update(), gr.update(), gr.update(), raw
        res = raw.get("res", [])
        if sel is None or sel >= len(res):
            gr.Warning("請先在下方點選一張影像，再按「◉ 單張計算」。")
            return gr.update(), gr.update(), gr.update(), raw
        one = _infer_all(weights, [res[sel]["path"]])
        res[sel] = one["res"][0]
        raw.get("ov", {}).pop(sel, None)
        head, gallery, items, table = _render(raw, thr)
        gr.Info(f"✅ 已重算第 {sel + 1} 張：{Path(res[sel]['path']).name}")
        return gr.update(value=table), items, gr.update(value=items[sel]["meta"], visible=True), raw

    def on_switch(cls, raw, sel, thr):
        if cls == SW_PLACEHOLDER:
            return gr.update(), gr.update(), gr.update(), raw, gr.update()
        if not raw.get("res") or sel is None:
            gr.Warning("請先點選一張結果，再選要切換的分類。")
            return gr.update(), gr.update(), gr.update(), raw, gr.update(value=SW_PLACEHOLDER)
        raw.setdefault("ov", {})[sel] = cls
        head, gallery, items, table = _render(raw, thr)
        gr.Info(f"⇄ 第 {sel + 1} 張改判為「{cls}」")
        return (gr.update(value=table), items, gr.update(value=items[sel]["meta"], visible=True),
                raw, gr.update(value=SW_PLACEHOLDER))

    def on_apply_thr(raw, thr):
        if not raw.get("res"):
            gr.Warning("請先按「▶ 批次計算」產生結果。")
            return gr.update(), gr.update(), gr.update()
        head, gallery, items, table = _render(raw, thr)
        return gr.update(value=table), items, gr.update(value=head, visible=True)

    def on_csv(raw, thr, request: gr.Request = None):
        from .. import auth
        if auth.public_locked(request):     # 寫 exports/infer_results.csv：訪客／預覽中一律擋
            gr.Warning(auth.locked_msg(request))
            return gr.update(visible=False)
        if not raw.get("res"):
            gr.Warning("請先按「▶ 批次計算」產生結果。")
            return gr.update(visible=False)
        fp = _export_csv(raw, thr)
        gr.Info("✅ 已匯出 CSV")
        return gr.update(value=fp, visible=True)

    def on_save(raw, thr, request: gr.Request = None):
        from .. import auth
        if auth.public_locked(request):
            gr.Warning(auth.locked_msg(request))
            return
        if not raw.get("res"):
            gr.Warning("請先按「▶ 批次計算」產生結果。")
            return
        n, dest = _save_sorted(raw, thr)
        gr.Info(f"💾 已另存 {n} 張 → {dest}")

    def on_select(items, evt: gr.SelectData):
        i = evt.index if evt is not None else None
        if not items or i is None or i >= len(items):
            return gr.update(), gr.update(), None
        it = items[i]
        return gr.update(value=it["img"]), gr.update(value=it["meta"], visible=True), i

    refresh_btn.click(on_refresh, [proj_dd], [proj_dd, run_dd])
    proj_dd.change(on_proj_change, [proj_dd], [run_dd, card_md])
    run_dd.change(on_run_change, [run_dd], [card_md])
    view_dd.change(on_view, [view_dd], [result_html])
    pick_btn.click(on_pick, None, [picked, count_md, result_gallery, res_state])
    infer_btn.click(on_infer, [run_dd, picked, thr],
                    [count_md, result_gallery, result_html, res_state, raw_state, switch_dd])
    single_btn.click(on_single, [run_dd, raw_state, sel_state, thr],
                     [result_html, res_state, preview_meta, raw_state])
    switch_dd.change(on_switch, [switch_dd, raw_state, sel_state, thr],
                     [result_html, res_state, preview_meta, raw_state, switch_dd])
    thr_btn.click(on_apply_thr, [raw_state, thr], [result_html, res_state, count_md])
    csv_btn.click(on_csv, [raw_state, thr], [csv_file])
    save_btn.click(on_save, [raw_state, thr], None)
    result_gallery.select(on_select, [res_state], [preview_img, preview_meta, sel_state])

    # 回傳供「訓練」分頁『用此模型推論』跨頁帶入專案/run；action 鈕供公開站訪客虛化
    return proj_dd, run_dd, pick_btn, infer_btn, single_btn, save_btn, csv_btn
