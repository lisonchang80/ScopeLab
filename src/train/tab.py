"""ScopeLab — 訓練分頁 UI（cls + det 統一）。

選專案（分類或偵測）→ 配方隨任務切換 → ▶ 開始訓練。事件用 **generator** 串流：
逐 epoch yield 更新 checkpoint 打勾、epoch 紀錄與進度條。依專案 task 分派
trainer_cls（torchvision ResNet）或 trainer_det（ultralytics YOLO）。
torch / 訓練引擎只在 handler 內惰性 import；store / recipes 輕量、建頁時 import。
"""
from __future__ import annotations

from datetime import datetime

import gradio as gr

from .. import store
from .recipes import CLS_RECIPES, DET_RECIPES, DEFAULT_RECIPE, recipe_choices

_PHASES = ["偵測裝置", "載入資料", "建立模型", "訓練", "產出 model_card"]


def _all_projects():
    out = []
    for c in store.list_projects():
        tn = "偵測" if c.get("task") == "det" else "分類"
        out.append((f"{c['name']}（{tn}·{len(c.get('classes', []))} 類）", c["slug"]))
    return out


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
            proj_dd = gr.Dropdown(_all_projects(), value=None, label="選擇專案（分類 cls / 偵測 det）",
                                  filterable=False, scale=4)
            refresh_btn = gr.Button("🔄", elem_classes="tbtn", scale=0, min_width=48)
            detect_btn = gr.Button("🔍 偵測裝置", elem_classes="tbtn", scale=0)
        recipe_radio = gr.Radio(recipe_choices("cls"), value=DEFAULT_RECIPE,
                                label="配方（隨任務切換）", elem_classes="ls-fmt")
        device_html = gr.HTML("<div class='infobar'>（按「偵測裝置」或開始訓練時自動偵測 GPU/CPU）</div>")
        with gr.Row(elem_classes="inforow"):
            train_btn = gr.Button("▶ 開始訓練", elem_classes="tbtn accent", scale=1)
            infer_jump_btn = gr.Button("🔍 用此模型推論（訓練完成後可用）", interactive=False,
                                       elem_classes="tbtn primary", scale=1)
        last_run = gr.State(None)
        with gr.Row(elem_classes="inforow"):
            check_html = gr.HTML(_checklist(set(), None))
            eplog_html = gr.HTML("")
        result_html = gr.HTML("")

    # ---- 事件 ----
    def on_refresh():
        return gr.update(choices=_all_projects())

    def on_proj_change(slug):
        task = (store.load_card(slug) or {}).get("task", "cls") if slug else "cls"
        return gr.update(choices=recipe_choices(task), value=DEFAULT_RECIPE)

    def on_detect():
        from . import device
        d = device.pick_device()
        return f"<div class='infobar'>{device.device_summary(d)}</div>"

    def on_train(slug, recipe_key, request: gr.Request = None, progress=gr.Progress()):
        from .. import auth
        if auth.public_locked(request):
            raise gr.Error(auth.locked_msg(request))
        if not slug:
            raise gr.Error("請先選一個專案。")
        from . import device
        card = store.load_card(slug) or {}
        task = card.get("task", "cls")
        classes = card.get("classes", [])
        labels = store.load_labels(slug).get("labels", {})
        files = list(labels.keys())
        if task == "cls" and len(classes) < 2:
            raise gr.Error("分類專案需至少 2 類。")
        if task == "det" and len(classes) < 1:
            raise gr.Error("偵測專案需至少 1 類。")

        dev = device.pick_device()
        out_dir = store.project_dir(slug) / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
        if task == "det":
            from . import trainer_det
            E = DET_RECIPES[recipe_key]["epochs"]
            gen = trainer_det.train_det(card.get("name", slug), files, labels, classes,
                                        recipe_key, dev, str(out_dir))
        else:
            from . import trainer_cls
            E = CLS_RECIPES[recipe_key]["epochs"]
            gen = trainer_cls.train_cls(card.get("name", slug), files, labels, classes,
                                        recipe_key, dev, str(out_dir))

        # 輸出順序：train_btn, infer_jump_btn, last_run, device_html, check_html, eplog_html, result_html
        done, current, eplog, last = set(), "偵測裝置", [], None
        dev_line = f"<div class='infobar'>{device.device_summary(dev)} · 任務 {task}</div>"
        progress(0.0, desc="準備中…")
        # 起手：鎖訓練鈕、推論鈕維持虛化
        yield (gr.update(interactive=False), gr.update(interactive=False), last,
               dev_line, _checklist(done, current), _epochlog(eplog), "")
        done.add("偵測裝置")
        current = "載入資料"

        for ev in gen:
            ph = ev["phase"]
            if ph == "error":
                yield (gr.update(interactive=True), gr.update(interactive=False), last,
                       dev_line, _checklist(done, None), _epochlog(eplog),
                       f"<div class='train-err'>❌ {ev['msg']}</div>")
                return
            if ph == "stopped":
                yield (gr.update(interactive=True), gr.update(interactive=False), last,
                       dev_line, _checklist(done, None), _epochlog(eplog),
                       "<div class='train-err'>⏹ 已中止。</div>")
                return
            if ph == "data":
                done.add("載入資料")
                current = "建立模型"
                eplog.append(f"資料：訓練 {ev['n_train']} · 驗證 {ev['n_val']} · {ev['n_classes']} 類")
            elif ph == "model":
                done.add("建立模型")
                current = "訓練"
                pre = "預訓練" if ev.get("pretrained") else "從頭訓"
                eplog.append(f"模型：{ev['arch']}（{pre}）· batch {ev['batch']}")
            elif ph == "epoch":
                tick = "✅" if ev["improved"] else "・"
                if "map50" in ev:                      # det
                    sc = f"mAP50 {ev['map50']:.3f} · best {ev['best_map50']:.3f}"
                else:                                  # cls
                    sc = f"acc {ev['val_acc']:.3f} · F1 {ev['val_f1']:.3f} · best {ev['best_acc']:.3f}"
                g = ev.get("gpu") or {}
                gtxt = (f"　[GPU {g['util']}% {g['temp']}°C {g['mem_used_gb']}/{g['mem_total_gb']}GB]"
                        if g.get("util") is not None else "")
                warn = ("　⚠️ " + "；".join(ev["warnings"])) if ev.get("warnings") else ""
                eplog.append(f"{tick} Epoch {ev['epoch']}/{ev['total']} · {sc}{gtxt}{warn}")
                progress(ev["epoch"] / max(1, ev["total"]), desc=f"Epoch {ev['epoch']}/{ev['total']}")
            elif ph == "done":
                done.update(["建立模型", "訓練", "產出 model_card"])
                current = None
                last = {"slug": slug, "weights": ev["weights"]}     # 帶給『用此模型推論』
                s = ev["summary"]
                if "best_map50" in s:
                    sname, score, arch = "mAP50", s["best_map50"], s.get("model", "")
                else:
                    sname, score, arch = "val acc", s["best_val_acc"], s.get("arch", "")
                result = (
                    "<div class='train-done'>✅ 訓練完成<br>"
                    f"{sname} <b>{score:.3f}</b> · {arch} · {s['epochs']} epoch<br>"
                    f"權重 <code>{ev['weights']}</code><br>"
                    f"識別卡 <code>{ev['model_card']}</code></div>")
                # 完成 → 解鎖訓練鈕＋推論鈕、寫入 last_run
                yield (gr.update(interactive=True), gr.update(interactive=True), last,
                       dev_line, _checklist(done, None), _epochlog(eplog), result)
                return
            # 進行中：訓練鈕鎖、推論鈕維持虛化、last 不變
            yield (gr.update(), gr.update(), last,
                   dev_line, _checklist(done, current), _epochlog(eplog), "")

    refresh_btn.click(on_refresh, None, proj_dd)
    proj_dd.change(on_proj_change, [proj_dd], [recipe_radio])
    detect_btn.click(on_detect, None, device_html)
    train_btn.click(on_train, [proj_dd, recipe_radio],
                    [train_btn, infer_jump_btn, last_run,
                     device_html, check_html, eplog_html, result_html])
    # 回傳供 app 跨頁接線：標記頁『開始訓練』帶入(proj_dd/recipe_radio)、
    # 訓練完『用此模型推論』帶出(infer_jump_btn/last_run)；train_btn 供公開站訪客虛化
    return proj_dd, recipe_radio, infer_jump_btn, last_run, train_btn
