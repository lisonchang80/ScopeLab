"""ScopeLab — 訓練分頁 UI（cls）。

選分類專案 → 選配方 → ▶ 開始訓練。事件用 **generator** 串流：逐 epoch `yield`
更新 checkpoint 打勾清單、epoch 紀錄與進度條。torch / 訓練引擎只在 handler 內惰性 import，
不拖慢 app 啟動；store / recipes 為輕量、可在建頁時 import。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import gradio as gr

from .. import store
from .recipes import RECIPES, DEFAULT_RECIPE, recipe_choices

_PHASES = ["偵測裝置", "載入資料", "建立模型", "訓練", "產出 model_card"]


def _cls_projects():
    return [(f"{c['name']}（{len(c.get('classes', []))} 類）", c["slug"])
            for c in store.list_projects() if c.get("task") == "cls"]


def _checklist(done: set, current: str | None) -> str:
    rows = []
    for s in _PHASES:
        mark = "✅" if s in done else ("🔄" if s == current else "⬜")
        rows.append(f"<div class='ck-row'>{mark}&nbsp;&nbsp;{s}</div>")
    return "<div class='train-check'>" + "".join(rows) + "</div>"


def _epochlog(lines: list[str]) -> str:
    if not lines:
        return ""
    body = "".join(f"<div class='ep-row'>{l}</div>" for l in lines[-12:])
    return f"<div class='train-eplog'><b>逐 epoch（✅＝刷新 best 並存檔）</b>{body}</div>"


def build_train_tab():
    with gr.Column(elem_classes="ls-panel"):
        gr.HTML('<div class="ls-modal-title">🏋 訓練　TRAIN</div>')
        with gr.Row(elem_classes="inforow"):
            proj_dd = gr.Dropdown(_cls_projects(), label="選擇分類專案（cls）",
                                  filterable=False, scale=4)
            refresh_btn = gr.Button("🔄", elem_classes="tbtn", scale=0, min_width=48)
            detect_btn = gr.Button("🔍 偵測裝置", elem_classes="tbtn", scale=0)
        recipe_radio = gr.Radio(recipe_choices(), value=DEFAULT_RECIPE,
                                label="配方", elem_classes="ls-fmt")
        device_html = gr.HTML("<div class='infobar'>（按「偵測裝置」或開始訓練時自動偵測 GPU/CPU）</div>")
        train_btn = gr.Button("▶ 開始訓練", elem_classes="tbtn accent")
        with gr.Row(elem_classes="inforow"):
            check_html = gr.HTML(_checklist(set(), None))
            eplog_html = gr.HTML("")
        result_html = gr.HTML("")

    # ---- 事件 ----
    def on_refresh():
        return gr.update(choices=_cls_projects())

    def on_detect():
        from . import device
        d = device.pick_device()
        return f"<div class='infobar'>{device.device_summary(d)}</div>"

    def on_train(slug, recipe_key, progress=gr.Progress()):
        if not slug:
            raise gr.Error("請先選一個分類專案（cls）。")
        from . import device, trainer_cls
        card = store.load_card(slug) or {}
        classes = card.get("classes", [])
        labels = store.load_labels(slug).get("labels", {})
        files = list(labels.keys())
        if len(classes) < 2:
            raise gr.Error("此專案類別少於 2 類，無法訓練分類器。")

        dev = device.pick_device()
        E = RECIPES[recipe_key]["epochs"]
        out_dir = store.project_dir(slug) / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")

        done, current, eplog = set(), "偵測裝置", []
        dev_line = f"<div class='infobar'>{device.device_summary(dev)}</div>"
        progress(0.0, desc="準備中…")
        # 起手：鎖按鈕、顯示裝置、清單就緒
        yield (gr.update(interactive=False), dev_line, _checklist(done, current),
               _epochlog(eplog), "")
        done.add("偵測裝置")
        current = "載入資料"

        for ev in trainer_cls.train_cls(card.get("name", slug), files, labels, classes,
                                        recipe_key, dev, str(out_dir)):
            ph = ev["phase"]
            if ph == "error":
                yield (gr.update(interactive=True), dev_line, _checklist(done, None),
                       _epochlog(eplog), f"<div class='train-err'>❌ {ev['msg']}</div>")
                return
            if ph == "stopped":
                yield (gr.update(interactive=True), dev_line, _checklist(done, None),
                       _epochlog(eplog), "<div class='train-err'>⏹ 已中止。</div>")
                return
            if ph == "data":
                done.add("載入資料")
                current = "建立模型"
                eplog.append(f"資料：訓練 {ev['n_train']} · 驗證 {ev['n_val']} · {ev['n_classes']} 類")
            elif ph == "model":
                done.add("建立模型")
                current = "訓練"
                pre = "預訓練" if ev["pretrained"] else "從頭訓"
                eplog.append(f"模型：{ev['arch']}（{pre}）· batch {ev['batch']}")
            elif ph == "epoch":
                tick = "✅" if ev["improved"] else "・"
                warn = ("　⚠️ " + "；".join(ev["warnings"])) if ev.get("warnings") else ""
                g = ev.get("gpu") or {}
                gtxt = (f"　[GPU {g['util']}% {g['temp']}°C {g['mem_used_gb']}/{g['mem_total_gb']}GB]"
                        if g.get("util") is not None else "")
                eplog.append(f"{tick} Epoch {ev['epoch']}/{ev['total']} · "
                             f"acc {ev['val_acc']:.3f} · F1 {ev['val_f1']:.3f} · "
                             f"best {ev['best_acc']:.3f}{gtxt}{warn}")
                progress(ev["epoch"] / ev["total"], desc=f"Epoch {ev['epoch']}/{ev['total']}")
            elif ph == "done":
                done.update(["訓練", "產出 model_card"])
                current = None
                s = ev["summary"]
                result_html_val = (
                    "<div class='train-done'>✅ 訓練完成<br>"
                    f"best val acc <b>{s['best_val_acc']:.3f}</b> · {s['arch']} · "
                    f"{s['epochs']} epoch<br>"
                    f"權重 <code>{ev['weights']}</code><br>"
                    f"識別卡 <code>{ev['model_card']}</code></div>")
                yield (gr.update(interactive=True), dev_line, _checklist(done, None),
                       _epochlog(eplog), result_html_val)
                return
            # 訓練進行中：按鈕維持鎖住（gr.update() 不更動 interactive）
            yield (gr.update(), dev_line, _checklist(done, current),
                   _epochlog(eplog), "")

    refresh_btn.click(on_refresh, None, proj_dd)
    detect_btn.click(on_detect, None, device_html)
    train_btn.click(on_train, [proj_dd, recipe_radio],
                    [train_btn, device_html, check_html, eplog_html, result_html])
