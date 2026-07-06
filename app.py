"""ScopeLab — 影像辨識標記工作台

沿用 ClinScope 的三視窗版面與海軍藍／檸檬黃視覺，但用途改為「建立訓練用標記資料集」：

  左上  基本資料 BASIC INFO   填寫要做的模型／資料集資訊（名稱、科別、任務 cls/det、類別…）
  右上  影像編輯 IMAGE EDIT   選取的影像；點圖放大進全螢幕編輯器（縮放/平移、裁切/旋轉/翻轉、
                              亮度/對比/銳化進階、偵測畫框），存回
  下方  分類   CLASSIFY        載入未分類影像 → 分頁分類 / 加類別(＋) / 逐張指派 → 匯出資料集

匯出可對接 ClinScope/MatScope（ImageFolder / YOLO）或通用格式（ZIP / COCO）。
"""
from __future__ import annotations

import html
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote as _quote

import gradio as gr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src import store, imaging, exporter, train, infer, auth
from src.store import TOTAL, NULL, DONE, TODO, ADDCLASS, TASKS

# 工具列『指派分類』下拉未選圖時顯示的提示（並虛化）；比照 ClinScope「⇄ 切換分類」
ASSIGN_HINT = "⇄ 指派分類"


# ============================================================================
#  狀態
# ============================================================================
def new_state(card: dict | None = None) -> dict:
    card = card or store.blank_card()
    return {
        "slug": card.get("slug") or "",
        "name": card.get("name") or "",
        "task": card.get("task") or "cls",
        "classes": list(card.get("classes") or []),
        "source_folder": card.get("source_folder") or "",
        "files": [],
        "labels": {},      # cls: path->類別 ; det: path->[{cls,x,y,w,h}]
        "edits": {},       # path->編輯後影像路徑
        "page": TOTAL,
        "view": "詳細資料",
        "selected": None,
        "page_files": [],
    }


def persist(S):
    """把標記寫回 labels.json（已建立專案才寫）。"""
    if S.get("slug"):
        store.save_labels(S["slug"], {"labels": S["labels"], "edits": S["edits"]})


# ============================================================================
#  分頁 / 篩選 / 計數
# ============================================================================
def _cls_of(labels, f):
    """取分類標記（只接受字串，避免切換任務時 list 殘留造成錯誤）。"""
    c = labels.get(f)
    return c if isinstance(c, str) else None


def _boxes_of(labels, f):
    """取偵測框（只接受 list）。"""
    b = labels.get(f)
    return b if isinstance(b, list) else []


def class_counts(S):
    files, labels, classes, task = S["files"], S["labels"], S["classes"], S["task"]
    per = {c: 0 for c in classes}
    if task == "cls":
        nullc = 0
        for f in files:
            c = _cls_of(labels, f)
            if c in per:
                per[c] += 1
            else:
                nullc += 1
        labeled = len(files) - nullc
        return {"per": per, "null": nullc, "labeled": labeled, "boxes": 0, "done": labeled}
    # det
    boxes = done = 0
    for f in files:
        bx = _boxes_of(labels, f)
        if bx:
            done += 1
            boxes += len(bx)
        for b in bx:
            if b.get("cls") in per:
                per[b["cls"]] += 1
    return {"per": per, "null": len(files) - done, "labeled": done, "boxes": boxes, "done": done}


def page_choices(S):
    cc = class_counts(S)
    n = len(S["files"])
    out = [(f"{TOTAL}（{n}）", TOTAL)]
    if S["task"] == "cls":
        out.append((f"{NULL}（{cc['null']}）", NULL))
    else:
        out.append((f"{DONE}（{cc['done']}）", DONE))
        out.append((f"{TODO}（{cc['null']}）", TODO))
    for c in S["classes"]:
        out.append((f"{c}（{cc['per'].get(c, 0)}）", c))
    out.append((ADDCLASS, ADDCLASS))
    return out


def page_files(S):
    files, labels, task, page = S["files"], S["labels"], S["task"], S["page"]
    if page == TOTAL:
        return list(files)
    if task == "cls":
        if page == NULL:
            return [f for f in files if _cls_of(labels, f) not in S["classes"]]
        return [f for f in files if _cls_of(labels, f) == page]
    # det
    if page == DONE:
        return [f for f in files if _boxes_of(labels, f)]
    if page == TODO:
        return [f for f in files if not _boxes_of(labels, f)]
    return [f for f in files if any(b.get("cls") == page for b in _boxes_of(labels, f))]


# ============================================================================
#  渲染：分頁 / 影像格 / 詳細表 / 狀態列
# ============================================================================
def _fsize(path):
    try:
        kb = os.path.getsize(path) / 1024
        return f"{kb/1024:.1f} MB" if kb >= 1024 else f"{kb:.0f} KB"
    except Exception:
        return "—"


def gallery_items(S):
    pf = page_files(S)
    S["page_files"] = pf
    items = []
    for f in pf:
        dp = imaging.display_path(f, S["edits"])
        if S["task"] == "cls":
            cap = store.Path(f).name
        else:
            nb = len(_boxes_of(S["labels"], f))
            cap = f"{store.Path(f).name} · {nb}框" if nb else store.Path(f).name
        items.append((dp, cap))
    return items


def _cls_select(i, cur, classes):
    opts = [f'<option value="" {"selected" if not cur else ""}>—未分類</option>']
    for c in classes:
        sel = "selected" if c == cur else ""
        opts.append(f'<option value="{html.escape(c)}" {sel}>{html.escape(c)}</option>')
    return f'<select class="ls-cls" data-i="{i}">{"".join(opts)}</select>'


def detail_html_str(S):
    pf = page_files(S)
    S["page_files"] = pf
    if not S["files"]:
        return ('<div class="ls-empty-state"><div class="es-ic">🏷️</div>'
                '<div class="es-t">尚未載入影像</div>'
                '<div class="es-s">先在左上「基本資料」<b>建立並儲存專案</b>，再按下方 '
                '<b>📁 選擇資料夾</b> 載入未分類影像開始標記。</div></div>')
    if not pf:
        return '<div class="ls-empty-state"><div class="es-ic">📭</div><div class="es-t">此分頁沒有影像</div></div>'

    if S["task"] == "cls":
        head = "<tr><th>檔名</th><th>分類</th><th>編輯</th><th>大小</th></tr>"
        rows = []
        for i, f in enumerate(pf):
            cur = _cls_of(S["labels"], f) or ""
            edited = "🖍" if f in S["edits"] else ""
            rows.append(
                f'<tr class="ls-row" data-i="{i}"><td class="ls-name" title="{html.escape(f)}">'
                f'{edited}{html.escape(store.Path(f).name)}</td>'
                f'<td>{_cls_select(i, cur, S["classes"])}</td>'
                f'<td><button class="ls-edit" data-i="{i}" title="放大編輯">✎</button></td>'
                f'<td>{_fsize(f)}</td></tr>')
    else:
        head = "<tr><th>檔名</th><th>物件</th><th>類別</th><th>編輯</th></tr>"
        rows = []
        for i, f in enumerate(pf):
            bx = _boxes_of(S["labels"], f)
            summ = {}
            for b in bx:
                summ[b.get("cls", "?")] = summ.get(b.get("cls", "?"), 0) + 1
            tag = "、".join(f"{k}×{v}" for k, v in summ.items()) or "—"
            edited = "🖍" if f in S["edits"] else ""
            rows.append(
                f'<tr class="ls-row" data-i="{i}"><td class="ls-name" title="{html.escape(f)}">'
                f'{edited}{html.escape(store.Path(f).name)}</td>'
                f'<td>{len(bx)}</td><td>{html.escape(tag)}</td>'
                f'<td><button class="ls-edit" data-i="{i}" title="放大標記">✎</button></td></tr>')
    return (f'<div class="ls-dt-wrap"><table class="ls-dt"><thead>{head}</thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def count_text(S):
    if not S["files"]:
        return gr.update(value="", visible=False)
    cc = class_counts(S)
    n = len(S["files"])
    chips = " · ".join(f"{c} {cc['per'].get(c,0)}" for c in S["classes"][:12]) or "（尚無類別）"
    if S["task"] == "cls":
        head = f"共 **{n}** 張 · 已分類 **{cc['labeled']}** · 未分類 **{cc['null']}**"
    else:
        head = f"共 **{n}** 張 · 已標記 **{cc['done']}** · 框 **{cc['boxes']}**"
    return gr.update(value=f"{head} ｜ {chips}", visible=True)


def info_text(S):
    if not S["slug"] and not S["files"]:
        return "尚未建立專案。填寫上方欄位後按「💾 儲存基本資料」。"
    cc = class_counts(S)
    n = len(S["files"])
    tag = "分類" if S["task"] == "cls" else "偵測"
    base = f"**{S['name'] or '（未命名）'}** · {tag} · 類別 {len(S['classes'])} · 影像 {n}"
    if n:
        base += f" · 已標記 {cc['labeled']}"
    return base


def render4(S):
    """回傳 [page_radio, gallery, detail_html, count_md] 的 update。"""
    items = gallery_items(S)          # 也會更新 S["page_files"]
    detail = detail_html_str(S)
    show_gallery = S["view"] == "圖示"
    return (
        gr.update(choices=page_choices(S), value=S["page"]),
        gr.update(value=items, visible=show_gallery),
        gr.update(value=detail, visible=not show_gallery),
        count_text(S),
    )


# ============================================================================
#  右上「影像編輯」視窗：預覽 + 給編輯器的資料
# ============================================================================
def assign_update(S):
    """工具列『指派分類』下拉狀態（比照 ClinScope「⇄ 切換分類」）：
    未選圖 / 非 cls → 顯示提示 ASSIGN_HINT 並虛化；已選圖 → 啟用、值為該圖目前分類。"""
    on = (S.get("task") == "cls") and bool(S.get("selected"))
    if not on:
        return gr.update(choices=[ASSIGN_HINT], value=ASSIGN_HINT, interactive=False)
    cur = _cls_of(S["labels"], S["selected"])
    return gr.update(choices=[ASSIGN_HINT] + list(S.get("classes") or []),
                     value=(cur or ASSIGN_HINT), interactive=True)


def preview_bundle(S, open_editor=False):
    """回傳 [preview_img, data_box(json), preview_meta, assign_dd]。"""
    f = S.get("selected")
    if not f:
        return (gr.update(value=None), gr.update(value="{}"),
                gr.update(value="選一張影像檢視；點圖可放大編輯。", visible=True),
                assign_update(S))
    dp = imaging.display_path(f, S["edits"])
    if S["task"] == "det":
        boxes = _boxes_of(S["labels"], f)
        img = imaging.draw_boxes(dp, boxes, S["classes"])
        meta = f"**{store.Path(f).name}** · {len(boxes)} 個框"
    else:
        boxes = []
        img = imaging.load_rgb(dp)
        cur = _cls_of(S["labels"], f) or "未分類"
        meta = f"**{store.Path(f).name}** · 分類：{cur}"
    payload = {
        "path": f, "url": imaging.file_url(dp), "task": S["task"],
        "classes": S["classes"], "boxes": boxes, "name": store.Path(f).name,
        "open": bool(open_editor),
        "cls": ((_cls_of(S["labels"], f) or "") if S["task"] == "cls" else ""),
    }
    return (gr.update(value=img), gr.update(value=json.dumps(payload, ensure_ascii=False)),
            gr.update(value=meta, visible=True), assign_update(S))


# ============================================================================
#  基本資料視窗 handler
# ============================================================================
def project_choices():
    return [(f"{c['name']}（{TASKS.get(c['task'],'')}）", c["slug"]) for c in store.list_projects()]


def on_page_projects(S):
    """頁面載入時重讀磁碟刷新專案下拉。
    Gradio 把元件 choices 凍在 build()（伺服器啟動）當下，session 中新建的專案
    只靠 gr.update 即時顯示，一旦 F5 重整就退回啟動快照而消失 → 每次載入重抓。"""
    choices = project_choices()
    vals = [v for _, v in choices]
    cur = S.get("slug") if S.get("slug") in vals else (choices[0][1] if choices else None)
    return gr.update(choices=choices, value=cur), gr.update(choices=choices, value=None)


def _form(card):
    cls_txt = "、".join(card.get("classes") or [])
    return (card.get("name", ""), card.get("discipline", ""), card.get("task", "cls"),
            card.get("author", ""), card.get("version", "0.1.0"),
            card.get("license", ""), card.get("description", ""), cls_txt)


def on_proj_change(slug, S):
    # 按「🆕 新專案」會把專案下拉設成 None，連帶觸發本 change 事件；
    # slug 為空時視同新專案，回空白表單而非去 load_card(None) 崩潰。
    card = store.load_card(slug) if slug else None
    if not card:
        return (S, *_form(store.blank_card()), *render4(S), info_text(S),
                *preview_bundle(S))
    data = store.load_labels(slug)
    S = new_state(card)
    S["labels"], S["edits"] = data.get("labels", {}), data.get("edits", {})
    if card.get("source_folder") and Path(card["source_folder"]).is_dir():
        S["files"] = store.scan_folder(card["source_folder"])
    return (S, *_form(card), *render4(S), info_text(S), *preview_bundle(S))


def on_new_project(S):
    card = store.blank_card()
    S = new_state(card)
    return (S, *_form(card), *render4(S), info_text(S), *preview_bundle(S),
            gr.update(value=None))


def _parse_classes(text):
    raw = (text or "").replace(",", "、").replace("，", "、").replace("\n", "、")
    seen, out = set(), []
    for c in (x.strip() for x in raw.split("、")):
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def on_save_card(name, disc, task, author, ver, lic, desc, classes_txt, S):
    if not (name or "").strip():
        raise gr.Error("請先填寫『專案名稱』。")
    card = store.load_card(S["slug"]) if S.get("slug") else store.blank_card()
    card.update({"name": name.strip(), "discipline": disc.strip(), "task": task,
                 "author": author.strip(), "version": ver.strip(), "license": lic.strip(),
                 "description": desc.strip(), "classes": _parse_classes(classes_txt),
                 "source_folder": S.get("source_folder", "")})
    card = store.save_card(card)
    S["slug"], S["name"], S["task"], S["classes"] = card["slug"], card["name"], card["task"], card["classes"]
    persist(S)
    gr.Info(f"✅ 已儲存專案「{card['name']}」→ projects/{card['slug']}/")
    return (S, gr.update(choices=project_choices(), value=card["slug"]),
            info_text(S), *render4(S), assign_update(S))


def on_task_change(task, S):
    S["task"], S["page"], S["selected"] = task, TOTAL, None
    return (S, *render4(S), assign_update(S), info_text(S))


# ============================================================================
#  分類視窗 handler
# ============================================================================
def on_pick_folder(S, request: gr.Request = None):
    if auth.public_locked(request):
        gr.Warning(auth.locked_msg(request))
        return (S, *render4(S), *preview_bundle(S), info_text(S))
    path = _native_dir()
    if not path:
        return (S, *render4(S), *preview_bundle(S), info_text(S))
    S["files"] = store.scan_folder(path)
    S["source_folder"], S["page"], S["selected"] = path, TOTAL, None
    if S.get("slug"):
        card = store.load_card(S["slug"]) or store.blank_card()
        card["source_folder"] = path
        store.save_card(card)
    n = len(S["files"])
    gr.Info(f"📂 已載入 {n} 張影像" if n else "⚠️ 此資料夾沒有支援的影像檔")
    return (S, *render4(S), *preview_bundle(S), info_text(S))


def _native_dir():
    # 是否允許開原生對話框由呼叫端（on_pick_folder → auth.public_locked）把關；
    # 站主開「本地模式」時走到這裡，就在站主本機彈出選夾。
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title="選擇未分類影像資料夾")
        try:
            root.destroy()
        except Exception:
            pass
        return path or ""
    except Exception:
        return ""


def on_view(view, S):
    S["view"] = (view or "👁 詳細資料").replace("👁 ", "")
    return (S, *render4(S))


def on_page(page, S):
    if page == ADDCLASS:               # 「＋」由前端 prompt 處理，後端不切換
        return (S, *render4(S), *preview_bundle(S))
    S["page"], S["selected"] = page, None
    return (S, *render4(S), *preview_bundle(S))


def on_assign(cls, S):
    """工具列『指派分類』下拉：把選取影像設為該類別（cls）。
    ASSIGN_HINT（提示值）與空值不視為指派。"""
    f = S.get("selected")
    if S["task"] == "cls" and f and cls and cls != ASSIGN_HINT and cls in S["classes"]:
        S["labels"][f] = cls
        persist(S)
    return (S, *render4(S), *preview_bundle(S), info_text(S))


def on_gallery_select(S, evt: gr.SelectData):
    pf = S.get("page_files") or page_files(S)
    if evt.index is not None and 0 <= evt.index < len(pf):
        S["selected"] = pf[evt.index]
    return (S, *preview_bundle(S))


# ---- bridge：詳細表選列 / 改分類 / 加類別 / 編輯器存回 ----------------------
BRIDGE_LEN = 11  # S, page_radio, gallery, detail, count, preview, data, meta, classes_tb, assign, info


def _noop(S):
    return (S, gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update())


def on_bridge(payload, S):
    try:
        obj = json.loads(payload or "{}")
    except Exception:
        return _noop(S)
    a = obj.get("a")
    pf = S.get("page_files") or page_files(S)

    def at(i):
        return pf[i] if (i is not None and 0 <= i < len(pf)) else None

    if a in ("sel", "sel_edit"):
        f = at(obj.get("i"))
        if f:
            S["selected"] = f
        pv = preview_bundle(S, open_editor=(a == "sel_edit"))
        out = list(_noop(S))
        out[5], out[6], out[7] = pv[0], pv[1], pv[2]   # preview, data, meta
        out[9] = pv[3]                                  # assign_dd 狀態（已選圖 → 啟用）
        return tuple(out)

    if a == "cls":
        f = at(obj.get("i"))
        if f is not None:
            p = obj.get("p") or ""
            if p:
                S["labels"][f] = p
            else:
                S["labels"].pop(f, None)
            S["selected"] = f
            persist(S)
        r = render4(S)
        pv = preview_bundle(S)
        return (S, r[0], r[1], r[2], r[3], pv[0], pv[1], pv[2],
                gr.update(), pv[3], info_text(S))

    if a == "addclass":
        name = (obj.get("name") or "").strip()
        if name and name not in S["classes"]:
            S["classes"].append(name)
            if S.get("slug"):
                card = store.load_card(S["slug"]) or store.blank_card()
                card["classes"] = S["classes"]
                store.save_card(card)
        r = render4(S)
        return (S, r[0], r[1], r[2], r[3], gr.update(), gr.update(), gr.update(),
                gr.update(value="、".join(S["classes"])),
                assign_update(S), info_text(S))

    if a == "editor_save":
        f = obj.get("path") or S.get("selected")
        if not f:
            return _noop(S)
        img = obj.get("img")
        if img:
            if not S.get("slug"):
                gr.Warning("像素編輯需先儲存專案；本次僅保留標記框。")
            else:
                try:
                    S["edits"][f] = imaging.save_dataurl(S["slug"], f, img)
                except Exception as e:
                    gr.Warning(f"存影像失敗：{e}")
        if S["task"] == "det":
            boxes = obj.get("boxes") or []
            if boxes:
                S["labels"][f] = boxes
            else:
                S["labels"].pop(f, None)
        S["selected"] = f
        persist(S)
        gr.Info("💾 已儲存編輯")
        r = render4(S)
        pv = preview_bundle(S)
        return (S, r[0], r[1], r[2], r[3], pv[0], pv[1], pv[2],
                gr.update(), pv[3], info_text(S))

    return _noop(S)


# ============================================================================
#  匯出
# ============================================================================
def export_formats(task):
    if task == "cls":
        return [("對接平台 — ImageFolder 資料夾", "cls_folder"),
                ("通用 — ZIP（分類資料夾）", "cls_zip")]
    return [("對接平台 — YOLO 資料夾", "det_yolo"),
            ("通用 — COCO（annotations.json）", "det_coco"),
            ("通用 — ZIP（YOLO）", "det_zip")]


def on_export_open(S):
    if not S["files"]:
        raise gr.Error("尚未載入影像。")
    if not S["classes"]:
        raise gr.Error("尚未定義任何類別（在基本資料填寫，或用分頁的「＋」新增）。")
    S["_ovw_fmt"] = None  # 清掉上次的覆蓋確認狀態
    fmts = export_formats(S["task"])
    return (gr.update(visible=True),
            gr.update(choices=fmts, value=fmts[0][1]),
            gr.update(value="", visible=False),     # 警告區清空
            gr.update(value="開始匯出"))            # 按鈕還原


def on_export_fmt_change(fmt, S):
    """切換格式 → 取消尚未確認的覆蓋、收起警告、還原按鈕。"""
    S["_ovw_fmt"] = None
    return (gr.update(value="", visible=False),
            gr.update(value="開始匯出"))


def on_export_confirm(fmt, S, progress=gr.Progress()):
    if not S.get("slug"):
        raise gr.Error("請先儲存專案再匯出。")

    card = store.load_card(S["slug"]) or store.blank_card()
    card["classes"] = S["classes"]
    name = card.get("name")

    # 重複匯出：目的地已存在且尚未針對此格式確認 → 先提示，要求再按一次確認
    if exporter.already_exists(S["slug"], fmt, name) and S.get("_ovw_fmt") != fmt:
        S["_ovw_fmt"] = fmt
        dest = exporter.dest_for(S["slug"], fmt, name)
        warn = (f'⚠️ 已存在舊的匯出 <code>{dest.name}</code>，'
                '再次匯出會<b>整個覆蓋</b>它（舊內容會被刪除）。<br>確定要覆蓋嗎？')
        return (gr.update(),                          # modal 保持開啟
                gr.update(),                          # 下載檔不變
                gr.update(value=warn, visible=True),  # 顯示警告
                gr.update(value="⚠️ 覆蓋並匯出"))     # 按鈕轉為確認

    # 執行匯出（本來就不存在，或使用者已確認覆蓋）
    S["_ovw_fmt"] = None
    try:
        path, stats, is_zip = exporter.run(
            S["slug"], card, S["files"], S["labels"], S["edits"], fmt, progress)
    except Exception as e:  # 占用/權限等 → 乾淨的錯誤提示，不讓 handler 崩潰
        raise gr.Error(f"匯出失敗：{e}")
    gr.Info(f"✅ 匯出完成：{stats.get('images',0)} 張"
            + (f" · {stats.get('boxes',0)} 框" if "boxes" in stats else "")
            + f" → {path}")
    # ⚠️ gr.File 只能吃「檔案」；資料夾匯出把目錄路徑塞進去會讓前端 File 元件
    #    丟 e.i18n is not a function 整頁凍住。只有 zip 才放下載檔，資料夾不放。
    file_update = (gr.update(value=path, visible=True) if is_zip
                   else gr.update(value=None, visible=False))
    return (gr.update(visible=False),               # 關閉 modal
            file_update,                            # 下載（僅 zip）
            gr.update(value="", visible=False),     # 清警告
            gr.update(value="開始匯出"))            # 按鈕還原


# ============================================================================
#  從「標記」頁直接開始訓練（建輸出資料夾 → 切到訓練頁 → 帶入專案/配方）
# ============================================================================
def on_start_train(S):
    slug = S.get("slug")
    if not slug:
        raise gr.Error("請先儲存基本資料（命名專案）後再開始訓練。")
    # 建立輸出資料夾（runs/，訓練結果會落在 runs/<時間戳>/）若還沒有
    (store.project_dir(slug) / "runs").mkdir(parents=True, exist_ok=True)
    from src.train.recipes import recipe_choices, DEFAULT_RECIPE
    task = S.get("task", "cls")
    gr.Info(f"🏋 已帶「{S.get('name') or slug}」到訓練分頁")
    return (gr.update(selected="train"),                          # 切換到訓練分頁
            gr.update(choices=train.tab._all_projects(), value=slug),  # 帶入專案
            gr.update(choices=recipe_choices(task), value=DEFAULT_RECIPE))  # 配方隨任務


def on_goto_infer(last_run):
    """訓練完成後『用此模型推論』：切到推論分頁、帶入該專案與剛訓好的 run。"""
    if not last_run or not last_run.get("weights"):
        raise gr.Error("尚無已訓練的模型，請先在訓練分頁完成一次訓練。")
    slug, weights = last_run["slug"], last_run["weights"]
    gr.Info("🔍 已帶剛訓練的模型到推論分頁")
    return (gr.update(selected="infer"),
            gr.update(choices=infer.tab._all_projects(), value=slug),
            gr.update(choices=infer.tab._runs(slug), value=weights))


# ============================================================================
#  刪除專案
# ============================================================================
def on_del_open(S):
    choices = project_choices()
    if not choices:
        raise gr.Error("目前沒有任何已儲存的專案可刪除。")
    # 不預選，強迫使用者刻意挑選要刪的專案（破壞性動作）
    return (gr.update(visible=True), gr.update(choices=choices, value=None))


def on_del_confirm(del_slug, S, request: gr.Request = None):
    auth.guard_owner(request)      # 縱深防禦：刪除為破壞性操作，訪客一律擋（UI 也已虛化）
    if not del_slug:
        raise gr.Error("請先選擇要刪除的專案。")
    name = next((c["name"] for c in store.list_projects() if c["slug"] == del_slug), del_slug)
    try:
        store.delete_project(del_slug)
    except Exception as e:  # 占用/權限等 → 乾淨錯誤提示，不讓 handler 崩潰
        raise gr.Error(f"刪除失敗：{e}")
    gr.Info(f"🗑 已刪除專案「{name}」")

    if S.get("slug") == del_slug:
        # 刪到目前開啟的專案 → 重置成空白新專案
        card = store.blank_card()
        S = new_state(card)
        return (gr.update(visible=False), gr.update(choices=project_choices(), value=None),
                S, *_form(card), *render4(S), info_text(S), *preview_bundle(S))
    # 刪的是其他專案 → 保留目前畫面，只刷新切換下拉
    card = store.load_card(S["slug"]) if S.get("slug") else store.blank_card()
    return (gr.update(visible=False),
            gr.update(choices=project_choices(), value=S.get("slug") or None),
            S, *_form(card), *render4(S), info_text(S), *preview_bundle(S))


# ============================================================================
#  外觀（沿用 ClinScope：海軍藍/白/檸檬黃）
# ============================================================================
THEME = gr.themes.Base(
    primary_hue=gr.themes.colors.blue,
    neutral_hue=gr.themes.colors.slate,
    font=[gr.themes.GoogleFont("IBM Plex Sans"), "ui-sans-serif", "system-ui", "sans-serif"],
    font_mono=[gr.themes.GoogleFont("IBM Plex Mono"), "ui-monospace", "monospace"],
)

CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&display=swap');
:root{
  --navy-900:#0B1F3A; --navy-700:#163C6B; --navy-500:#22568F; --navy-100:#E5EDF7;
  --paper:#F4F8FD; --surface:#FFFFFF; --ink:#0B1F3A; --muted:#5B708A;
  --lemon:#FFE14D; --lemon-soft:#FFF7C4; --line:#D8E3F0;
}
.gradio-container{background:var(--paper)!important; color:var(--ink);
  max-width:100%!important; width:100%!important; padding:8px 16px!important;
  height:100vh!important; overflow:hidden!important;}
.gradio-container .main, .gradio-container .main.fillable{
  max-width:100%!important; width:100%!important; margin:0!important; padding:0!important;
  height:100%!important; min-height:0!important; overflow:hidden!important;}
.main.fillable > .wrap,
.main.fillable > .wrap > .contain,
.main.fillable > .wrap > .contain > .column{flex:1 1 auto!important; min-height:0!important; overflow:hidden!important;}
.main.fillable > .wrap > .contain > .column{gap:10px!important;}
.contain > .column > .block{flex:0 0 auto!important; margin:0!important;}
/* 三分頁：gr.Tabs / 作用中 tabitem / 其內層 column 撐滿主欄，恢復 toprow+editrow 高度鏈
   （只設 flex/min-height，不碰 display，以免掀開 Gradio 用 display:none 隱藏的非作用分頁） */
.main.fillable .tabs{flex:1 1 auto!important; min-height:0!important;
  display:flex!important; flex-direction:column!important; overflow:hidden!important;}
.main.fillable .tabs > .tab-nav{flex:0 0 auto!important;}
.main.fillable .tabs > .tabitem{flex:1 1 auto!important; min-height:0!important; overflow:hidden!important;}
.main.fillable .tabs > .tabitem > .column{flex:1 1 auto!important; min-height:0!important;
  overflow:auto!important; gap:10px!important;}
/* 上排固定較矮的高度 → 基本資料表單內部捲動、把剩餘高度讓給下方分類區 */
.toprow{flex:0 0 auto!important; height:30vh!important; min-height:0!important; align-items:stretch!important;}
.toprow > .column, .toprow .ls-panel{height:100%!important; min-height:0!important;}
.editrow{flex:1 1 0!important; min-height:0!important; align-items:stretch!important;}
/* 面板內部子元素間距縮小：Gradio 預設 gap 太大會吃掉表格/影像格可用高度 */
.editrow .ls-panel{gap:4px!important;}
/* 捲軸常駐顯示（Windows 預設隱藏式捲軸，使用者會「看不到」誤以為不能捲） */
.infoscroll::-webkit-scrollbar, .ls-dt-wrap::-webkit-scrollbar, .grid::-webkit-scrollbar{width:11px; height:11px;}
.infoscroll::-webkit-scrollbar-thumb, .ls-dt-wrap::-webkit-scrollbar-thumb, .grid::-webkit-scrollbar-thumb{
  background:var(--navy-500); border-radius:6px; border:2px solid #fff;}
.infoscroll::-webkit-scrollbar-track, .ls-dt-wrap::-webkit-scrollbar-track, .grid::-webkit-scrollbar-track{background:var(--navy-100);}
.infoscroll, .ls-dt-wrap, .grid{scrollbar-width:thin; scrollbar-color:var(--navy-500) var(--navy-100);}
.editrow > .column, .editrow .ls-panel{min-height:0!important; height:100%!important;}
footer{display:none!important;}

/* 品牌列 */
.brandwrap, .brandwrap.prose, .brandwrap .html-container{padding:0!important; margin:0!important;
  border:none!important; background:transparent!important;}
.brandbar{display:flex; justify-content:space-between; align-items:center;
  background:linear-gradient(100deg,var(--navy-900),var(--navy-700)); color:#fff;
  padding:14px 22px; border-radius:14px; margin:0 0 14px 0; box-shadow:0 6px 22px rgba(11,31,58,.20);}
.brandbar .bmeta{display:flex; align-items:center; gap:8px; flex-wrap:wrap; justify-content:flex-end;}
.brandbar .brand{display:flex; align-items:center; gap:14px;}
.brandbar .logo{color:var(--lemon); font-size:28px;}
.brandbar .btext{display:inline-flex; flex-direction:column; align-items:stretch;}
.brandbar .bt{font-family:'Space Grotesk',sans-serif; font-weight:700; font-size:36px; letter-spacing:3px;
  color:var(--lemon)!important; text-shadow:0 1px 3px rgba(0,0,0,.30); line-height:1.05;}
/* 中文副標兩端對齊撐到與英文標題同寬（btext flex 欄 align-items:stretch + bsub 被 blockify→justify 生效）*/
.brandbar .bsub{color:#E3EEFB; font-size:15px; letter-spacing:0;
  text-align:justify; text-align-last:justify; margin-top:2px;}
.brandbar .chip{background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.22);
  padding:4px 12px; border-radius:999px; font-size:12px; font-family:'IBM Plex Mono',monospace;}
.brandbar .chip.lemon{background:var(--lemon); color:var(--navy-900); border:none; font-weight:600;}
.brandbar .dlbtn{display:inline-flex; align-items:center; gap:6px; background:var(--lemon);
  color:var(--navy-900)!important; font-weight:700; font-size:13px; text-decoration:none;
  padding:6px 14px; border-radius:999px; box-shadow:0 2px 8px rgba(0,0,0,.20);
  transition:transform .08s ease, box-shadow .08s ease;}
.brandbar .dlbtn:hover{transform:translateY(-1px); box-shadow:0 5px 14px rgba(0,0,0,.30);}

/* 三大視窗面板（class 用 ls-panel：Gradio 會吃掉保留字 panel） */
.ls-panel{background:var(--surface)!important; border:2px solid var(--navy-500)!important;
  border-radius:14px!important; padding:10px 14px 12px!important;
  box-shadow:0 6px 20px rgba(11,31,58,.13); display:flex!important; flex-direction:column!important; min-height:0!important;}
.phead{display:flex; justify-content:space-between; align-items:center; flex:0 0 auto;
  background:linear-gradient(95deg,var(--navy-900),var(--navy-700));
  margin:-10px -14px 10px -14px; padding:7px 14px; border-radius:11px 11px 0 0; border-bottom:3px solid var(--lemon);}
.phead .ph-tag{font-family:'Space Grotesk',sans-serif; font-weight:700; font-size:19px; color:var(--lemon)!important;}
.phead .ph-en{color:#9FC0E6; font-size:11px; letter-spacing:2px; margin-left:8px; font-family:'IBM Plex Mono',monospace;}
.phead .ph-sub{color:#CFE0F2; font-size:11.5px;}

/* 基本資料表單：壓縮間距 + 內部捲動（panel 固定高，phead 不動、表單區捲） */
.infoscroll{flex:1 1 0!important; min-height:0!important; overflow-y:auto!important; overflow-x:hidden!important;
  display:flex!important; flex-direction:column!important; gap:7px!important; padding-right:6px;}
/* 整列單一欄位（專案名稱／類別清單／說明）會被 Gradio 包進 .form 包裝層；
   在固定高度的 flex 欄裡，flex-shrink 會把這些 .form 壓成 ~10px → 幾乎看不見
   （.inforow 列因內容撐住不受影響）。只針對 .form：不收縮、保留自然高度，
   超出由 .infoscroll 內部捲動。 */
.infoscroll > .form{flex:0 0 auto!important;}
.infowin .gap{gap:7px!important;}
.infowin .block{padding:0!important; min-width:0!important;}
.inforow{gap:8px!important;}
.infowin label, .infowin .wrap{font-size:13px!important;}
.infowin textarea{font-size:13px!important;}
.infobar{font-size:12.5px; color:var(--navy-700); background:#F2F7FE; border:1px solid var(--line);
  border-radius:8px; padding:6px 11px!important; margin-top:4px;}
.infobar strong{color:var(--navy-900);}

/* 影像編輯預覽 */
.preview{border-radius:12px!important; overflow:hidden; background:#fff; border:1px dashed var(--line)!important;
  flex:1 1 0!important; min-height:150px!important;}
.preview img{object-fit:contain!important; cursor:zoom-in;}
.pmeta{font-size:13px; color:var(--ink); margin-top:6px; min-height:22px;}
.pmeta strong{color:var(--navy-700);}
.edithint{font-size:11.5px; color:var(--muted); margin-top:2px;}

/* 工具列 */
.toolbar{gap:5px!important; flex-wrap:wrap; align-items:center; margin-bottom:4px; justify-content:flex-start!important;}
.toolbar > *{flex:0 0 auto!important; margin:0!important;}
.tbtn{min-width:0!important; flex:0 0 auto!important; border-radius:10px!important;
  border:1px solid var(--line)!important; background:#fff!important; color:var(--navy-700)!important;
  font-weight:600!important; box-shadow:none!important; transition:transform .08s,box-shadow .12s;}
.tbtn:hover{transform:translateY(-1px); box-shadow:0 4px 12px rgba(11,31,58,.12); border-color:var(--navy-500)!important;}
.tbtn.primary{background:var(--navy-700)!important; color:var(--lemon)!important; border-color:var(--navy-700)!important;}
.tbtn.primary:hover{background:var(--navy-900)!important;}
.tbtn.accent{background:var(--lemon)!important; color:var(--navy-900)!important; border-color:var(--lemon)!important;}
.tbtn.navy2{background:var(--navy-500)!important; color:#fff!important; border-color:var(--navy-500)!important;}
/* 危險動作：開啟刪除對話框=紅框；真正執行刪除=紅底 */
.tbtn.danger{background:#fff!important; color:#C0392B!important; border-color:#E0A6A0!important;}
.tbtn.danger:hover{background:#FBECEA!important; border-color:#C0392B!important;}
.tbtn.danger-solid{background:#E74C3C!important; color:#fff!important; border-color:#E74C3C!important;}
.tbtn.danger-solid:hover{background:#C0392B!important; border-color:#C0392B!important;}
/* 專案下拉旁的「新專案／刪除專案」直向疊放 */
.projbtns{gap:5px!important; align-self:flex-end;}
.projbtns .tbtn{width:100%!important;}
.shortdd{min-width:118px!important; max-width:160px!important; flex:0 0 auto!important;}
.shortdd .wrap, .shortdd input{font-size:13px!important;}
/* 『指派分類』未選圖時虛化（比照 ClinScope；disable / readonly 兩種狀態都涵蓋） */
.shortdd input:disabled, .shortdd input[disabled], .assigndd input[readonly]{
  opacity:.5!important; cursor:not-allowed;}

.countbar{font-size:12.5px; color:var(--navy-700); background:#F2F7FE; border:1px solid var(--line);
  border-radius:8px; padding:6px 12px!important; margin:6px 0;}

/* 分頁 */
.tabstrip{overflow-x:auto; white-space:nowrap; border-bottom:2px solid var(--line); margin-bottom:0!important;}
.tabstrip .wrap{flex-wrap:nowrap!important; gap:2px!important;}
.tabstrip label{border:1.5px solid var(--navy-500)!important; background:#fff!important; border-radius:8px 8px 0 0!important;
  padding:7px 14px!important; color:var(--navy-700)!important; font-weight:600!important; cursor:pointer;
  box-shadow:none!important; margin:0 3px 0 0!important;}
.tabstrip label input{display:none!important;}
.tabstrip label:hover{background:var(--navy-100)!important; border-color:var(--navy-700)!important;}
.tabstrip label:has(input:checked){background:var(--navy-700)!important; color:#fff!important;
  border-color:var(--navy-700)!important; box-shadow:inset 0 -3px 0 var(--lemon)!important;}
.tabstrip label:has(input[value="＋"]){font-size:20px!important; font-weight:700!important; line-height:1!important;
  padding:5px 13px!important; display:inline-flex!important; align-items:center!important; justify-content:center!important;}

/* 影像格 / 詳細表 */
.grid{background:#fff!important; border:1px solid var(--line)!important; border-radius:12px!important;
  padding:6px!important; flex:1 1 0!important; min-height:0!important; height:auto!important; overflow:auto!important; margin-top:-16px!important;}
.grid .grid-wrap, .grid .thumbnails{min-height:0!important; height:100%!important;}
.grid .thumbnail-item{border-radius:10px!important; overflow:hidden;}
.grid .thumbnail-item.selected, .grid .selected{outline:3px solid var(--lemon)!important; outline-offset:-1px;}
.grid .caption, .grid .caption-label{font-family:'IBM Plex Mono',monospace!important; font-size:11px!important;
  background:var(--navy-900)!important; color:#fff!important;}
/* detail 捲動鏈（Gradio 把 elem_classes 同掛在外層 .block 與內層 .prose）：
   外層 block = bound 的 flex 直欄框；內層 prose = 填滿並成為捲動容器（thead sticky 黏在它的捲port） */
.block.detail{flex:1 1 0!important; min-height:0!important; overflow:hidden!important; padding:0!important;
  border:1px solid var(--line)!important; border-radius:12px!important; background:#fff!important; margin-top:-16px!important;
  display:flex!important; flex-direction:column!important;}
/* block 與 prose 之間 Gradio 還夾一層 .html-container（預設 display:block 不撐高）→ 也 flex 化 */
.block.detail > .html-container{flex:1 1 0!important; min-height:0!important; display:flex!important;
  flex-direction:column!important; overflow:hidden!important; padding:0!important;}
.prose.detail{flex:1 1 0!important; min-height:0!important; max-width:none!important; overflow:auto!important;
  padding:0!important; margin:0!important; border-radius:12px;}
.ls-dt-wrap{height:auto;}
.ls-dt{width:100%; border-collapse:collapse; table-layout:fixed; font-size:13px;}
.ls-dt thead th{position:sticky; top:0; z-index:3; background:var(--navy-100); color:var(--navy-900);
  text-align:left; padding:7px 10px; border-bottom:2px solid var(--navy-500); font-weight:700; white-space:nowrap;}
.ls-dt th:nth-child(1),.ls-dt td:nth-child(1){width:48%;}
.ls-dt th:nth-child(2),.ls-dt td:nth-child(2){width:26%;}
.ls-dt th:nth-child(3),.ls-dt td:nth-child(3){width:13%;}
.ls-dt th:nth-child(4),.ls-dt td:nth-child(4){width:13%;}
.ls-dt td{padding:3px 10px; border-bottom:1px solid var(--line); color:var(--ink); white-space:nowrap;}
.ls-dt td.ls-name{overflow:hidden; text-overflow:ellipsis; font-family:'IBM Plex Mono',monospace; font-size:12px;}
.ls-dt.det th:nth-child(2),.ls-dt.det td:nth-child(2){width:14%;}   /* 偵測表:框數窄、偵測項寬 */
.ls-dt.det th:nth-child(3),.ls-dt.det td:nth-child(3){width:38%;}
.infer-top{color:var(--navy-500); font-size:12px; white-space:normal;}  /* Top-3 / 偵測項:柔色、可換行 */
.infer-bad{color:#C0392B; font-weight:600;}
.infer-edit{color:#1F6FB2; font-weight:600;}   /* 手動切換分類後的標示 */
.ls-row{cursor:pointer;}
.ls-row:hover{background:var(--navy-100);}
.ls-row.ls-sel-row{background:var(--lemon-soft)!important; box-shadow:inset 3px 0 0 var(--lemon);}
.ls-cls{width:100%; max-width:100%; font-size:12.5px; padding:2px 4px; cursor:pointer;
  border:1.5px solid var(--navy-500); border-radius:6px; background:#fff; color:var(--navy-700); font-weight:600;}
.ls-edit{border:1px solid var(--navy-500); background:#fff; color:var(--navy-700); border-radius:6px;
  padding:1px 8px; cursor:pointer; font-size:13px;}
.ls-edit:hover{background:var(--navy-700); color:#fff;}
.ls-empty-state{height:100%; min-height:120px; display:flex; flex-direction:column;
  align-items:center; justify-content:center; text-align:center; gap:8px; padding:20px; box-sizing:border-box;}
.ls-empty-state .es-ic{font-size:46px; line-height:1; opacity:.85;}
.ls-empty-state .es-t{font-family:'Space Grotesk',sans-serif; font-size:19px; font-weight:700; color:var(--navy-700);}
.ls-empty-state .es-s{font-size:13.5px; color:var(--muted); max-width:460px; line-height:1.6;}
.ls-empty-state .es-s b{color:var(--navy-700);}
#ls-bridge, #ls-trigger, #ls-data, #ls-postedit{display:none!important;}

/* 公開站身分列 + 站主「本地模式」開關 */
#sl-identity{margin:2px 0 0;}
#sl-identity:empty{display:none;}
.sl-idbar{font-size:12.5px;font-weight:600;padding:5px 12px;border-radius:8px;
  display:inline-block;border:1px solid var(--line);}
.sl-idbar.sl-owner{background:#E7F6EC;color:#1B7A3D;border-color:#B7E3C6;}
.sl-idbar.sl-guest{background:var(--lemon-soft,#FBF3CF);color:#7A5B00;border-color:#F0DC8A;}
.sl-authbtn{margin-left:10px;padding:2px 12px;border-radius:7px;border:1px solid currentColor;
  background:transparent;color:inherit;font-weight:700;font-size:12px;cursor:pointer;vertical-align:middle;}
.sl-authbtn:hover{background:rgba(0,0,0,.06);}
.sl-gbtn{display:inline-block;vertical-align:middle;margin-left:4px;}
.sl-localchk{flex:0 0 auto!important;margin:4px 0 0!important;padding:0!important;
  background:transparent!important;border:none!important;box-shadow:none!important;min-width:0!important;}
.sl-localchk label{gap:6px!important;font-size:12.5px!important;font-weight:700!important;
  color:var(--navy-700)!important;white-space:nowrap;}

/* 匯出對話框 */
.ls-modal-backdrop{position:fixed!important; inset:0!important; z-index:9999!important;
  background:rgba(11,31,58,.45)!important; display:flex!important; align-items:center!important; justify-content:center!important;}
.ls-modal-backdrop.hide{display:none!important;}
.ls-modal-box{flex:0 0 auto!important; width:430px!important; max-width:92vw!important;
  background:#fff!important; border:2px solid var(--navy-500)!important; border-radius:14px!important;
  padding:20px 22px!important; gap:14px!important; box-shadow:0 18px 60px rgba(11,31,58,.45)!important;}
.ls-modal-title{font-family:'Space Grotesk',sans-serif; font-size:17px; font-weight:700; color:var(--navy-900);}
.ls-fmt .wrap{flex-direction:column!important; gap:7px!important; flex-wrap:nowrap!important;}
.ls-fmt label{border:1.5px solid var(--line)!important; background:#fff!important; border-radius:9px!important;
  padding:9px 12px!important; color:var(--navy-700)!important; font-weight:600!important; cursor:pointer;
  display:flex!important; align-items:center; gap:8px; box-shadow:none!important; margin:0!important;}
.ls-fmt label:has(input:checked){background:var(--lemon-soft)!important; border-color:var(--navy-500)!important;
  box-shadow:inset 3px 0 0 var(--lemon)!important;}
.ls-modal-warn{border:1.5px solid #E8B84B!important; background:#FFF7E0!important;
  border-radius:9px!important; padding:9px 12px!important; color:#7A5A12!important;
  font-size:13px!important; line-height:1.5!important;}
.ls-modal-warn code{background:#fff; border:1px solid #E8C77A; border-radius:5px;
  padding:1px 5px; font-size:12px;}
.ls-modal-actions{gap:10px!important; justify-content:flex-end!important;}

.foot{text-align:center; color:var(--muted); font-size:11px; margin-top:4px;
  border-top:1px solid var(--line); padding-top:5px;}
.foot b{color:var(--navy-700);}

/* ===== 全螢幕影像編輯器 ===== */
.ls-ed{position:fixed; inset:0; z-index:100000; background:rgba(11,31,58,.97);
  display:none; flex-direction:column; color:#EAF2FB;}
.ls-ed.on{display:flex;}
.ls-ed-bar{display:flex; align-items:center; gap:6px; flex-wrap:wrap;
  padding:9px 14px; background:linear-gradient(95deg,var(--navy-900),var(--navy-700)); border-bottom:3px solid var(--lemon);}
.ls-ed-bar .ttl{font-family:'Space Grotesk',sans-serif; font-weight:700; color:var(--lemon); font-size:16px;}
.ls-ed-bar .nm{font-family:'IBM Plex Mono',monospace; font-size:12px; color:#cfe0f2; margin-right:auto;
  max-width:26vw; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
.ls-ed-bar .sep{width:1px; height:22px; background:rgba(255,255,255,.2); margin:0 3px;}
.ls-eb{border:1px solid rgba(255,255,255,.25); background:rgba(255,255,255,.08); color:#fff;
  border-radius:9px; padding:6px 10px; font-size:13px; font-weight:600; cursor:pointer; white-space:nowrap;}
.ls-eb:hover{background:rgba(255,255,255,.18);}
.ls-eb.on{background:var(--lemon); color:var(--navy-900); border-color:var(--lemon);}
.ls-eb.save{background:var(--lemon); color:var(--navy-900); border-color:var(--lemon);}
.ls-eb.ghost{background:transparent;}
.ls-ed-cls{border-radius:9px; padding:6px 8px; font-size:13px; font-weight:600;
  border:1.5px solid var(--lemon); background:#fff; color:var(--navy-900);}
.ls-ed-stage{flex:1 1 0; min-height:0; position:relative; overflow:hidden;}
.ls-ed-cv{position:absolute; inset:0; width:100%; height:100%; touch-action:none;}
.ls-ed-adv{display:none; gap:20px; align-items:center; flex-wrap:wrap; padding:9px 16px;
  background:rgba(11,31,58,.92); border-top:1px solid rgba(255,255,255,.15);}
.ls-ed-adv.on{display:flex;}
.ls-ed-adv label{font-size:12px; color:#cfe0f2; display:flex; align-items:center; gap:8px;}
.ls-ed-adv input[type=range]{accent-color:var(--lemon); width:150px;}
.ls-ed-adv .val{font-family:'IBM Plex Mono',monospace; width:34px; text-align:right;}
.ls-ed-foot{display:flex; gap:14px; align-items:center; padding:6px 16px; font-size:11.5px; color:#9FC0E6;
  background:rgba(11,31,58,.92);}

@media (max-width:768px){
  .gradio-container{height:auto!important; overflow:visible!important; padding:6px 10px!important;}
  .gradio-container .main, .gradio-container .main.fillable{height:auto!important; min-height:0!important; overflow:visible!important;}
  .main.fillable > .wrap, .main.fillable > .wrap > .contain, .main.fillable > .wrap > .contain > .column{
    height:auto!important; overflow:visible!important; flex:0 0 auto!important;}
  .toprow, .editrow{flex:0 0 auto!important; height:auto!important;}
  .infoscroll{overflow:visible!important;}
  .editrow > .column, .editrow .ls-panel{height:auto!important; min-height:0!important;}
  .preview{height:240px!important;}
  .grid, .detail{flex:0 0 auto!important; height:auto!important; max-height:60vh!important; overflow:auto!important;}
  .brandbar{flex-wrap:wrap; gap:8px;}
}
"""


def panel_head(tag, en, sub):
    return (f'<div class="phead"><div><span class="ph-tag">{tag}</span>'
            f'<span class="ph-en">{en}</span></div><span class="ph-sub">{sub}</span></div>')


# 「下載桌面版」按鈕：只有公開站（packaging/dist 下存在安裝檔）才顯示；桌面版自身不顯示。
_SETUP_EXE = Path(__file__).resolve().parent / "packaging" / "dist" / "ScopeLab-Setup.exe"
SETUP_DL_URL = ("/gradio_api/file=" + _quote(_SETUP_EXE.as_posix())) if _SETUP_EXE.exists() else None
_DL_BTN = (
    f'<a class="dlbtn" href="{SETUP_DL_URL}" download="ScopeLab-Setup.exe" '
    f'title="下載 Windows 桌面安裝版（離線標記／訓練／推論；裝到自己機器上用自家 GPU 與資料夾）">'
    f'⬇ 下載桌面版</a>') if SETUP_DL_URL else ''
_PUBLIC_CHIP = ('<span class="chip" style="background:#C0392B;border:none;color:#fff;">🔒 公開展示模式</span>'
                if store.PUBLIC else '')

BRAND_HTML = (
    '<div class="brandbar"><div class="brand"><span class="logo">🏷️</span>'
    '<span class="btext"><span class="bt">ScopeLab</span>'
    '<span class="bsub">影像辨識標記工作台</span></span></div>'
    f'<div class="bmeta">{_DL_BTN}{_PUBLIC_CHIP}<span class="chip">分類 · 偵測</span>'
    '<span class="chip lemon">建立訓練資料集</span></div></div>')

FOOTER_HTML = ('<div class="foot">影像標記 → 匯出 ImageFolder / YOLO / COCO，'
               '可直接對接 <b>ClinScope</b> / <b>MatScope</b> 訓練流程。</div>')


# ============================================================================
#  前端互動 + 全螢幕編輯器（純 JS）
# ============================================================================
INIT_JS = r"""
() => {
  if (window.__ls_init) return; window.__ls_init = true; window.__ls_idx = 0;

  /* ---------- bridge ---------- */
  const bridgeEl = () => document.querySelector('#ls-bridge textarea, #ls-bridge input');
  const dataEl   = () => document.querySelector('#ls-data textarea, #ls-data input');
  const setNative = (el, v) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const d = Object.getOwnPropertyDescriptor(proto, 'value'); if (d && d.set) d.set.call(el, v); else el.value = v;
    el.dispatchEvent(new Event('input', {bubbles: true}));
  };
  const send = (obj) => {
    const el = bridgeEl(); if (!el) return;
    window.__ls_n = (window.__ls_n || 0) + 1; obj.n = window.__ls_n;
    setNative(el, JSON.stringify(obj));
    const b = document.querySelector('#ls-trigger button') || document.getElementById('ls-trigger');
    if (b) b.click();
  };
  const readData = () => { try { return JSON.parse((dataEl() || {}).value || '{}'); } catch (e) { return {}; } };

  /* ---------- 詳細表 / 影像格 互動 ---------- */
  const detailMode = () => { const d = document.querySelector('.detail'); return d && d.offsetParent !== null; };
  const galItems = () => { const g = document.querySelector('.grid');
    return (g && g.offsetParent !== null) ? Array.from(g.querySelectorAll('button.thumbnail-item, .thumbnail-item')) : null; };
  const detailRows = () => Array.from(document.querySelectorAll('.detail .ls-row'));
  const highlight = (row) => { document.querySelectorAll('.detail .ls-row.ls-sel-row').forEach(r => r.classList.remove('ls-sel-row'));
    if (row) row.classList.add('ls-sel-row'); };
  const selRow = (row) => { if (!row) return; const i = parseInt(row.dataset.i, 10);
    window.__ls_idx = i; highlight(row); row.scrollIntoView({block: 'nearest'}); send({a: 'sel', i}); };

  document.addEventListener('click', (e) => {
    if (!e.target.closest) return;
    const tab = e.target.closest('.tabstrip label');
    if (tab && tab.textContent.trim() === '＋') {
      e.preventDefault(); e.stopPropagation();
      const name = (window.prompt('輸入新類別名稱') || '').trim();
      if (name) send({a: 'addclass', name}); return;
    }
    const eb = e.target.closest('.ls-edit');
    if (eb) { e.preventDefault(); e.stopPropagation(); send({a: 'sel_edit', i: parseInt(eb.dataset.i, 10)}); return; }
    if (e.target.closest('.detail .ls-cls')) return;
    const row = e.target.closest('.detail .ls-row');
    if (row) { selRow(row); return; }
  }, true);

  document.addEventListener('change', (e) => {
    const sel = e.target.closest ? e.target.closest('.detail .ls-cls') : null;
    if (sel) { const i = parseInt(sel.dataset.i, 10); window.__ls_idx = i; send({a: 'cls', i, p: sel.value}); }
  });

  /* 鍵盤：方向鍵切換、數字鍵 1–9 指派分類（分類模式） */
  document.addEventListener('keydown', (e) => {
    if (document.querySelector('.ls-ed.on')) return;          // 編輯器開啟時不攔
    const t = (document.activeElement || {}).tagName || '';
    if (t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT') return;
    if (/^[1-9]$/.test(e.key)) {
      const d = readData();
      if (d.task === 'cls' && Array.isArray(d.classes)) {
        const c = d.classes[parseInt(e.key, 10) - 1];
        if (c) { send({a: 'cls', i: window.__ls_idx | 0, p: c}); e.preventDefault(); }
      }
      return;
    }
    if (!['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown'].includes(e.key)) return;
    const fwd = (e.key === 'ArrowRight' || e.key === 'ArrowDown');
    if (detailMode()) { const rows = detailRows(); if (!rows.length) return;
      const i = Math.max(0, Math.min(rows.length - 1, (window.__ls_idx | 0) + (fwd ? 1 : -1)));
      selRow(rows[i]); e.preventDefault(); return; }
    const it = galItems(); if (!it || !it.length) return;
    const i = Math.max(0, Math.min(it.length - 1, (window.__ls_idx | 0) + (fwd ? 1 : -1)));
    window.__ls_idx = i; it[i].click(); it[i].scrollIntoView({block: 'nearest'}); e.preventDefault();
  });

  /* ================= 全螢幕影像編輯器 ================= */
  const ED = {open: false, task: 'cls', classes: [], path: '', name: '', boxes: [], sel: -1,
              mode: 'pan', curClass: '', view: {zoom: 1, ox: 0, oy: 0},
              enh: {b: 1, c: 1, s: 0}, dirty: false, base: null, enhc: null, drag: null};

  const el = (t, cls, html) => { const e = document.createElement(t); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };

  function buildUI() {
    const root = el('div', 'ls-ed');
    const bar = el('div', 'ls-ed-bar');
    bar.innerHTML =
      '<span class="ttl">🖍 編輯</span><span class="nm" data-k="nm"></span>' +
      '<button class="ls-eb" data-k="rotl" title="左轉 90°">⟲</button>' +
      '<button class="ls-eb" data-k="rotr" title="右轉 90°">⟳</button>' +
      '<button class="ls-eb" data-k="fliph" title="水平翻轉">↔</button>' +
      '<button class="ls-eb" data-k="flipv" title="垂直翻轉">↕</button>' +
      '<span class="sep"></span>' +
      '<button class="ls-eb" data-k="crop" title="裁切：拖曳框選後套用">▣ 裁切</button>' +
      '<button class="ls-eb" data-k="cropapply" style="display:none">✓ 套用裁切</button>' +
      '<button class="ls-eb" data-k="adv" title="亮度/對比/銳化">⚙ 進階</button>' +
      '<span class="sep" data-k="detsep" style="display:none"></span>' +
      '<button class="ls-eb" data-k="box" style="display:none" title="畫框：拖曳新增">▢ 畫框</button>' +
      '<select class="ls-ed-cls" data-k="cls" style="display:none"></select>' +
      '<button class="ls-eb" data-k="del" style="display:none" title="刪除選取框">🗑</button>' +
      '<span class="sep"></span>' +
      '<button class="ls-eb" data-k="fit" title="符合視窗">⤢ 1:1</button>' +
      '<button class="ls-eb save" data-k="save">💾 儲存</button>' +
      '<button class="ls-eb ghost" data-k="close">✕ 關閉</button>';
    const stage = el('div', 'ls-ed-stage');
    const cv = el('canvas', 'ls-ed-cv');
    stage.appendChild(cv);
    const adv = el('div', 'ls-ed-adv');
    adv.innerHTML =
      '<label>亮度 <input type="range" data-k="b" min="0.3" max="1.8" step="0.02" value="1"><span class="val" data-k="bv">1.0</span></label>' +
      '<label>對比 <input type="range" data-k="c" min="0.3" max="1.8" step="0.02" value="1"><span class="val" data-k="cv">1.0</span></label>' +
      '<label>銳化 <input type="range" data-k="s" min="0" max="1" step="0.05" value="0"><span class="val" data-k="sv">0.0</span></label>' +
      '<button class="ls-eb" data-k="enhreset">重設</button>';
    const foot = el('div', 'ls-ed-foot',
      '滾輪縮放 · 拖曳平移 · 方向：左下角狀態 · 偵測：選「畫框」後拖曳新增、點框選取、🗑 或 Delete 刪除 · ESC 關閉');
    root.append(bar, stage, adv, foot);
    document.body.appendChild(root);
    ED.dom = {root, bar, stage, cv, adv, foot, ctx: cv.getContext('2d')};
    wireUI();
    return ED.dom;
  }

  function q(k) { return ED.dom.root.querySelector('[data-k="' + k + '"]'); }

  function wireUI() {
    const on = (k, fn, ev) => q(k).addEventListener(ev || 'click', fn);
    on('close', closeEd); on('save', saveEd);
    on('rotl', () => rotate(-1)); on('rotr', () => rotate(1));
    on('fliph', () => flip('h')); on('flipv', () => flip('v'));
    on('fit', () => { ED.view = {zoom: 1, ox: 0, oy: 0}; redraw(); });
    on('adv', () => { ED.dom.adv.classList.toggle('on'); q('adv').classList.toggle('on'); });
    on('crop', () => setMode(ED.mode === 'crop' ? 'pan' : 'crop'));
    on('cropapply', applyCrop);
    on('box', () => setMode(ED.mode === 'box' ? 'pan' : 'box'));
    on('del', deleteSel);
    on('cls', (e) => { ED.curClass = e.target.value; if (ED.sel >= 0) { ED.boxes[ED.sel].cls = e.target.value; redraw(); } }, 'change');
    ['b', 'c', 's'].forEach(k => q(k).addEventListener('input', (e) => {
      ED.enh[k] = parseFloat(e.target.value); q(k + 'v').textContent = ED.enh[k].toFixed(k === 's' ? 1 : 1);
      ED.enhDirty = true; ED.dirty = true; scheduleEnh();
    }));
    on('enhreset', () => { ED.enh = {b: 1, c: 1, s: 0}; ['b', 'c', 's'].forEach(k => { q(k).value = ED.enh[k]; q(k + 'v').textContent = ED.enh[k].toFixed(1); });
      ED.enhDirty = true; buildEnh(); redraw(); });
    const cv = ED.dom.cv;
    cv.addEventListener('wheel', onWheel, {passive: false});
    cv.addEventListener('pointerdown', onDown); cv.addEventListener('pointermove', onMove);
    cv.addEventListener('pointerup', onUp); cv.addEventListener('pointerleave', onUp);
    window.addEventListener('resize', () => { if (ED.open) redraw(); });
    document.addEventListener('keydown', (e) => {
      if (!ED.open) return;
      if (e.key === 'Escape') closeEd();
      else if ((e.key === 'Delete' || e.key === 'Backspace') && ED.sel >= 0) { deleteSel(); e.preventDefault(); }
    });
  }

  function setMode(m) {
    ED.mode = m; ED.cropRect = null;
    q('crop').classList.toggle('on', m === 'crop');
    q('box').classList.toggle('on', m === 'box');
    q('cropapply').style.display = (m === 'crop') ? '' : 'none';
    ED.dom.cv.style.cursor = (m === 'pan') ? 'grab' : 'crosshair';
    redraw();
  }

  /* ---- 幾何編輯（直接改 base canvas + 變換框座標） ---- */
  function mapBoxes(fn) { ED.boxes = ED.boxes.map(b => {
    const p = [[b.x, b.y], [b.x + b.w, b.y + b.h]].map(([x, y]) => fn(x, y));
    const xs = p.map(q => q[0]), ys = p.map(q => q[1]);
    const x0 = Math.min(...xs), y0 = Math.min(...ys);
    return {cls: b.cls, x: x0, y: y0, w: Math.max(...xs) - x0, h: Math.max(...ys) - y0};
  }); }

  function rotate(dir) {
    const s = ED.base, w = s.width, h = s.height;
    const c = document.createElement('canvas'); c.width = h; c.height = w;
    const x = c.getContext('2d'); x.translate(h / 2, w / 2); x.rotate(dir * Math.PI / 2);
    x.drawImage(s, -w / 2, -h / 2); ED.base = c;
    if (dir > 0) mapBoxes((px, py) => [1 - py, px]); else mapBoxes((px, py) => [py, 1 - px]);
    ED.dirty = true; buildEnh(); redraw();
  }
  function flip(ax) {
    const s = ED.base, c = document.createElement('canvas'); c.width = s.width; c.height = s.height;
    const x = c.getContext('2d');
    if (ax === 'h') { x.translate(s.width, 0); x.scale(-1, 1); } else { x.translate(0, s.height); x.scale(1, -1); }
    x.drawImage(s, 0, 0); ED.base = c;
    if (ax === 'h') mapBoxes((px, py) => [1 - px, py]); else mapBoxes((px, py) => [px, 1 - py]);
    ED.dirty = true; buildEnh(); redraw();
  }
  function applyCrop() {
    if (!ED.cropRect) { setMode('pan'); return; }
    const r = ED.cropRect, s = ED.base;
    const cw = Math.max(1, Math.round(r.w * s.width)), ch = Math.max(1, Math.round(r.h * s.height));
    const c = document.createElement('canvas'); c.width = cw; c.height = ch;
    c.getContext('2d').drawImage(s, Math.round(r.x * s.width), Math.round(r.y * s.height), cw, ch, 0, 0, cw, ch);
    ED.base = c;
    ED.boxes = ED.boxes.map(b => ({cls: b.cls, x: (b.x - r.x) / r.w, y: (b.y - r.y) / r.h, w: b.w / r.w, h: b.h / r.h}))
      .map(b => ({cls: b.cls, x: Math.max(0, b.x), y: Math.max(0, b.y),
        w: Math.min(1, b.x + b.w) - Math.max(0, b.x), h: Math.min(1, b.y + b.h) - Math.max(0, b.y)}))
      .filter(b => b.w > 0.005 && b.h > 0.005);
    ED.cropRect = null; ED.dirty = true; ED.view = {zoom: 1, ox: 0, oy: 0}; buildEnh(); setMode('pan');
  }

  /* ---- 亮度/對比/銳化 ---- */
  function buildEnh() {
    const s = ED.base; const c = ED.enhc || document.createElement('canvas');
    c.width = s.width; c.height = s.height; const x = c.getContext('2d');
    x.filter = 'brightness(' + ED.enh.b + ') contrast(' + ED.enh.c + ')';
    x.drawImage(s, 0, 0); x.filter = 'none';
    if (ED.enh.s > 0) sharpen(x, c.width, c.height, ED.enh.s);
    ED.enhc = c; ED.enhDirty = false;
  }
  function sharpen(ctx, w, h, amt) {
    const src = ctx.getImageData(0, 0, w, h), out = ctx.createImageData(w, h);
    const a = amt, k = [0, -a, 0, -a, 1 + 4 * a, -a, 0, -a, 0], d = src.data, o = out.data;
    for (let y = 0; y < h; y++) for (let xx = 0; xx < w; xx++) {
      for (let ch = 0; ch < 3; ch++) {
        let sum = 0, ki = 0;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const px = Math.min(w - 1, Math.max(0, xx + dx)), py = Math.min(h - 1, Math.max(0, y + dy));
          sum += d[(py * w + px) * 4 + ch] * k[ki++];
        }
        o[(y * w + xx) * 4 + ch] = Math.max(0, Math.min(255, sum));
      }
      o[(y * w + xx) * 4 + 3] = d[(y * w + xx) * 4 + 3];
    }
    ctx.putImageData(out, 0, 0);
  }
  let enhTimer = null;
  function scheduleEnh() { clearTimeout(enhTimer); enhTimer = setTimeout(() => { buildEnh(); redraw(); }, 120); }

  /* ---- 視圖變換 / 繪製 ---- */
  function fit() {
    const st = ED.dom.stage.getBoundingClientRect(), s = ED.base;
    return Math.min(st.width / s.width, st.height / s.height) * 0.98;
  }
  function geom() {
    const st = ED.dom.stage.getBoundingClientRect(), s = ED.base;
    const sc = fit() * ED.view.zoom, dw = s.width * sc, dh = s.height * sc;
    const cx = st.width / 2 + ED.view.ox, cy = st.height / 2 + ED.view.oy;
    return {sc, dw, dh, ix0: cx - dw / 2, iy0: cy - dh / 2, sw: st.width, sh: st.height};
  }
  const toNorm = (cx, cy) => { const g = geom(); return [(cx - g.ix0) / g.dw, (cy - g.iy0) / g.dh]; };
  const toCanvas = (nx, ny) => { const g = geom(); return [g.ix0 + nx * g.dw, g.iy0 + ny * g.dh]; };

  function redraw() {
    if (!ED.base || !ED.dom) return;   // 影像尚未載入：略過（setMode 早於 onload）
    const dpr = window.devicePixelRatio || 1, cv = ED.dom.cv, ctx = ED.dom.ctx;
    const st = ED.dom.stage.getBoundingClientRect();
    cv.width = Math.round(st.width * dpr); cv.height = Math.round(st.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, st.width, st.height);
    const g = geom(); ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(ED.enhc || ED.base, g.ix0, g.iy0, g.dw, g.dh);
    // 框
    ED.boxes.forEach((b, i) => {
      const [x0, y0] = toCanvas(b.x, b.y), [x1, y1] = toCanvas(b.x + b.w, b.y + b.h);
      const col = colorOf(b.cls); ctx.lineWidth = (i === ED.sel) ? 3 : 2;
      ctx.strokeStyle = col; ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
      ctx.fillStyle = col; const tw = ctx.measureText(b.cls).width + 10;
      ctx.fillRect(x0, y0 - 17, tw, 17); ctx.fillStyle = '#0B1F3A';
      ctx.font = '12px IBM Plex Mono, monospace'; ctx.fillText(b.cls || '?', x0 + 5, y0 - 5);
      if (i === ED.sel) { ctx.fillStyle = '#FFE14D'; [[x0,y0],[x1,y0],[x0,y1],[x1,y1]].forEach(([hx,hy]) => ctx.fillRect(hx-4,hy-4,8,8)); }
    });
    if (ED.cropRect) { const r = ED.cropRect, [x0,y0] = toCanvas(r.x,r.y), [x1,y1] = toCanvas(r.x+r.w,r.y+r.h);
      ctx.save(); ctx.fillStyle = 'rgba(11,31,58,.55)'; ctx.fillRect(0,0,g.sw,g.sh); ctx.clearRect(x0,y0,x1-x0,y1-y0);
      ctx.drawImage(ED.enhc || ED.base, (x0-g.ix0)/g.sc, (y0-g.iy0)/g.sc, (x1-x0)/g.sc, (y1-y0)/g.sc, x0, y0, x1-x0, y1-y0);
      ctx.strokeStyle = '#FFE14D'; ctx.lineWidth = 2; ctx.setLineDash([6,4]); ctx.strokeRect(x0,y0,x1-x0,y1-y0); ctx.restore(); }
    const stt = q('nm'); if (stt) stt.dataset.zoom = (ED.view.zoom).toFixed(2);
    ED.dom.foot.dataset.z = Math.round(fit() * ED.view.zoom * 100);
    ED.dom.foot.firstChild && (ED.dom.foot.textContent = '🔍 ' + Math.round(ED.view.zoom * 100) + '%  ·  '
      + ED.base.width + '×' + ED.base.height + ' px  ·  滾輪縮放 · 拖曳平移 · 偵測：畫框後拖曳新增、點框選取、Delete 刪除 · ESC 關閉');
  }

  const PALETTE = ['#FFE14D','#4DD0E1','#FF8A65','#81C784','#BA68C8','#F06292','#64B5F6','#FFD54F','#A1887F','#90A4AE'];
  function colorOf(c) { const i = ED.classes.indexOf(c); return PALETTE[(i < 0 ? 0 : i) % PALETTE.length]; }

  function onWheel(e) {
    e.preventDefault(); const r = ED.dom.cv.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const [nx, ny] = toNorm(mx, my); const old = ED.view.zoom;
    ED.view.zoom = Math.max(0.2, Math.min(12, old * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
    const [cx, cy] = toCanvas(nx, ny); ED.view.ox += mx - cx; ED.view.oy += my - cy; redraw();
  }
  function hit(nx, ny) { for (let i = ED.boxes.length - 1; i >= 0; i--) { const b = ED.boxes[i];
    if (nx >= b.x && nx <= b.x + b.w && ny >= b.y && ny <= b.y + b.h) return i; } return -1; }
  function onDown(e) {
    const r = ED.dom.cv.getBoundingClientRect(); const mx = e.clientX - r.left, my = e.clientY - r.top;
    const [nx, ny] = toNorm(mx, my); try { ED.dom.cv.setPointerCapture(e.pointerId); } catch (_) {}
    if (ED.mode === 'pan') { ED.drag = {kind: 'pan', sx: mx, sy: my, ox: ED.view.ox, oy: ED.view.oy}; ED.dom.cv.style.cursor = 'grabbing'; }
    else if (ED.mode === 'crop') { ED.drag = {kind: 'crop', x: nx, y: ny}; ED.cropRect = {x: nx, y: ny, w: 0, h: 0}; }
    else if (ED.mode === 'box') { const h = hit(nx, ny);
      if (h >= 0) { ED.sel = h; ED.drag = {kind: 'move', i: h, x: nx, y: ny, bx: ED.boxes[h].x, by: ED.boxes[h].y}; redraw(); }
      else { ED.drag = {kind: 'draw', x: nx, y: ny}; ED.boxes.push({cls: ED.curClass || (ED.classes[0] || '物件'), x: nx, y: ny, w: 0, h: 0}); ED.sel = ED.boxes.length - 1; } }
  }
  function onMove(e) {
    if (!ED.drag) return; const r = ED.dom.cv.getBoundingClientRect(); const mx = e.clientX - r.left, my = e.clientY - r.top;
    const [nx, ny] = toNorm(mx, my); const d = ED.drag;
    if (d.kind === 'pan') { ED.view.ox = d.ox + (mx - d.sx); ED.view.oy = d.oy + (my - d.sy); redraw(); }
    else if (d.kind === 'crop') { ED.cropRect = {x: Math.min(d.x, nx), y: Math.min(d.y, ny), w: Math.abs(nx - d.x), h: Math.abs(ny - d.y)}; redraw(); }
    else if (d.kind === 'draw') { const b = ED.boxes[ED.sel];
      b.x = Math.min(d.x, nx); b.y = Math.min(d.y, ny); b.w = Math.abs(nx - d.x); b.h = Math.abs(ny - d.y); redraw(); }
    else if (d.kind === 'move') { const b = ED.boxes[d.i]; b.x = Math.max(0, Math.min(1 - b.w, d.bx + (nx - d.x))); b.y = Math.max(0, Math.min(1 - b.h, d.by + (ny - d.y))); redraw(); }
  }
  function onUp(e) {
    if (ED.drag && ED.drag.kind === 'draw') { const b = ED.boxes[ED.sel];
      if (b.w < 0.01 || b.h < 0.01) { ED.boxes.splice(ED.sel, 1); ED.sel = -1; } redraw(); }
    if (ED.drag && ED.drag.kind === 'pan') ED.dom.cv.style.cursor = 'grab';
    ED.drag = null;
  }
  function deleteSel() { if (ED.sel >= 0) { ED.boxes.splice(ED.sel, 1); ED.sel = -1; redraw(); } }

  function refreshClsSelect() {
    const sel = q('cls'); sel.innerHTML = '';
    ED.classes.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; sel.appendChild(o); });
    ED.curClass = ED.classes[0] || ''; sel.value = ED.curClass;
  }

  function openEd(d) {
    if (!ED.dom) buildUI();
    ED.task = d.task; ED.classes = d.classes || []; ED.path = d.path; ED.name = d.name || '';
    ED.boxes = (d.boxes || []).map(b => ({cls: b.cls, x: b.x, y: b.y, w: b.w, h: b.h}));
    ED.sel = -1; ED.mode = 'pan'; ED.view = {zoom: 1, ox: 0, oy: 0}; ED.enh = {b: 1, c: 1, s: 0}; ED.dirty = false;
    ['b', 'c', 's'].forEach(k => { q(k).value = ED.enh[k]; q(k + 'v').textContent = ED.enh[k].toFixed(1); });
    q('nm').textContent = ED.name;
    const det = (d.task === 'det');
    ['detsep', 'box', 'cls', 'del'].forEach(k => q(k).style.display = det ? '' : 'none');
    if (det) refreshClsSelect();
    ED.dom.adv.classList.remove('on'); q('adv').classList.remove('on');
    setMode('pan');
    const img = new Image();
    img.onload = () => {
      const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
      c.getContext('2d').drawImage(img, 0, 0); ED.base = c; ED.enhc = null; buildEnh();
      ED.dom.root.classList.add('on'); ED.open = true; requestAnimationFrame(redraw);
    };
    img.onerror = () => { console.error('影像載入失敗：' + d.url); };
    img.src = d.url;
  }
  function closeEd() { if (ED.dom) ED.dom.root.classList.remove('on'); ED.open = false; }

  function saveEd() {
    let img = null;
    if (ED.dirty) { buildEnh(); img = (ED.enhc || ED.base).toDataURL('image/png'); }
    const boxes = ED.boxes.filter(b => b.w > 0.004 && b.h > 0.004)
      .map(b => ({cls: b.cls, x: +b.x.toFixed(5), y: +b.y.toFixed(5), w: +b.w.toFixed(5), h: +b.h.toFixed(5)}));
    send({a: 'editor_save', path: ED.path, img, boxes});
    closeEd();
  }

  /* 由「🔍 放大編輯」鈕 / 點預覽圖 / 詳細表 ✎ → sel_edit 後開啟 */
  window.lsOpenEditor = () => { const d = readData(); if (d && d.url) openEd(d); };

  /* 點右上預覽圖 → 開編輯器 */
  document.addEventListener('click', (e) => {
    const img = e.target.closest && e.target.closest('.preview img');
    if (img) { e.preventDefault(); e.stopPropagation(); window.lsOpenEditor(); }
  }, true);
}
"""

# bridge 完成後若 data.open 為真 → 自動開啟編輯器（詳細表 ✎ / 程式選取後）
POST_BRIDGE_JS = r"""
() => { try { const e = document.querySelector('#ls-data textarea, #ls-data input');
  const d = JSON.parse((e || {}).value || '{}'); if (d.open && window.lsOpenEditor) window.lsOpenEditor(); } catch (x) {} }
"""


# ============================================================================
#  介面組裝
# ============================================================================
def build():
    # theme / css 在 Gradio 6 改由 launch() 傳入（見 launch_app）
    with gr.Blocks(title="ScopeLab — 影像辨識標記工作台") as demo:
        gr.HTML(BRAND_HTML, elem_classes="brandwrap")
        # 公開站身分列（由 auth.identity_bar 於載入時填）+ 站主專屬「本地模式」開關（預設隱藏）。
        identity_html = gr.HTML("", elem_id="sl-identity")
        local_chk = gr.Checkbox(False, label="🔓 本地模式（用本機 GPU / 資料夾）", visible=False,
                                elem_id="sl-local", elem_classes="sl-localchk", container=False)

        projs = project_choices()
        init_slug = projs[0][1] if projs else ""
        init_card = store.load_card(init_slug) if init_slug else store.blank_card()
        if init_slug:
            data = store.load_labels(init_slug)
            S0 = new_state(init_card)
            S0["labels"], S0["edits"] = data.get("labels", {}), data.get("edits", {})
            if init_card.get("source_folder") and Path(init_card["source_folder"]).is_dir():
                S0["files"] = store.scan_folder(init_card["source_folder"])
        else:
            S0 = new_state(init_card)
        S = gr.State(S0)

        with gr.Tabs() as tabs:
            with gr.Tab("🏷 標記", id="label"):
                # ---- 上排：基本資料(左) + 影像編輯(右) ----
                with gr.Row(equal_height=False, elem_classes="toprow"):
                    with gr.Column(scale=5, elem_classes="ls-panel infowin"):
                        gr.HTML(panel_head("基本資料", "BASIC INFO", "要做的模型 / 資料集資訊"))
                        with gr.Column(elem_classes="infoscroll"):
                            with gr.Row(elem_classes="inforow"):
                                proj_dd = gr.Dropdown(projs, value=init_slug or None, label="切換專案（選既有的）",
                                                      filterable=False, scale=4)
                                with gr.Column(scale=0, min_width=128, elem_classes="projbtns"):
                                    new_btn = gr.Button("🆕 新專案", elem_classes="tbtn", scale=0)
                                    del_btn = gr.Button("🗑 刪除專案", elem_classes="tbtn danger", scale=0)
                            name_tb = gr.Textbox(init_card.get("name", ""), label="專案名稱（新專案在這裡命名）",
                                                 placeholder="例：周邊血球五分類")
                            with gr.Row(elem_classes="inforow"):
                                disc_tb = gr.Textbox(init_card.get("discipline", ""), label="科別 / 領域", placeholder="血液 / 材料…", scale=3)
                                task_radio = gr.Radio([("分類", "cls"), ("偵測", "det")], value=init_card.get("task", "cls"),
                                                      label="任務類型", scale=2)
                            cls_tb = gr.Textbox("、".join(init_card.get("classes") or []), label="類別清單（、或逗號分隔）",
                                                placeholder="中性球、淋巴球、單核球…", lines=1)
                            with gr.Row(elem_classes="inforow"):
                                author_tb = gr.Textbox(init_card.get("author", ""), label="作者", scale=3)
                                ver_tb = gr.Textbox(init_card.get("version", "0.1.0"), label="版本", scale=2)
                            with gr.Row(elem_classes="inforow"):
                                lic_tb = gr.Textbox(init_card.get("license", ""), label="授權", scale=3)
                                save_card_btn = gr.Button("💾 儲存基本資料", elem_classes="tbtn primary", scale=2)
                            desc_tb = gr.Textbox(init_card.get("description", ""), label="說明", lines=2,
                                                 placeholder="資料來源、染色 / 拍攝條件、用途…")
                            info_md = gr.Markdown(info_text(S0), elem_classes="infobar")

                    with gr.Column(scale=6, elem_classes="ls-panel"):
                        gr.HTML(panel_head("影像編輯", "IMAGE EDIT", "點圖即放大 → 縮放/裁切/旋轉/增強/畫框 → 存回"))
                        preview_img = gr.Image(show_label=False, elem_classes="preview")
                        preview_meta = gr.Markdown("選一張影像檢視；點圖即放大編輯。", elem_classes="pmeta")
                        gr.HTML('<div class="edithint">點影像放大編輯 · 方向鍵切換影像 · 分類模式數字鍵 1–9 指派類別</div>')

                # ---- 下排：分類(滿版) ----
                with gr.Row(elem_classes="editrow"):
                    with gr.Column(elem_classes="ls-panel"):
                        gr.HTML(panel_head("分類", "CLASSIFY", "載入未分類影像 · 分頁分類 · ＋ 新增類別 · 匯出"))
                        with gr.Row(elem_classes="toolbar"):
                            folder_btn = gr.Button("📁 選擇資料夾", elem_classes="tbtn", scale=0)
                            view_dd = gr.Dropdown(["👁 詳細資料", "👁 圖示"], value="👁 詳細資料",
                                                  show_label=False, container=False, elem_classes="shortdd")
                            # 比照 ClinScope「⇄ 切換分類」：提示文字當下拉值、未選圖虛化、與按鈕同列
                            assign_dd = gr.Dropdown([ASSIGN_HINT], value=ASSIGN_HINT, show_label=False,
                                                    container=False, interactive=False,
                                                    elem_classes="shortdd assigndd")
                            export_btn = gr.Button("💾 匯出資料集", elem_classes="tbtn accent", scale=0)
                            start_train_btn = gr.Button("🏋 開始訓練", elem_classes="tbtn primary", scale=0)
                        count_md = gr.Markdown("", visible=False, elem_classes="countbar")
                        page_radio = gr.Radio(page_choices(S0), value=TOTAL, show_label=False,
                                              container=False, elem_classes="tabstrip")
                        gallery = gr.Gallery(show_label=False, columns=8, object_fit="cover",
                                             allow_preview=False, elem_classes="grid")
                        detail_html = gr.HTML(detail_html_str(S0), elem_classes="detail")
                        bridge = gr.Textbox("", elem_id="ls-bridge", show_label=False)
                        bridge_btn = gr.Button("bridge", elem_id="ls-trigger")
                        data_box = gr.Textbox("{}", elem_id="ls-data", show_label=False)
                        export_file = gr.File(label="下載", visible=False)

                # ---- 匯出對話框 ----
                with gr.Column(visible=False, elem_classes="ls-modal-backdrop") as export_modal:
                    with gr.Column(elem_classes="ls-modal-box"):
                        gr.HTML('<div class="ls-modal-title">💾 匯出資料集</div>')
                        export_fmt = gr.Radio(export_formats(S0["task"]), value=export_formats(S0["task"])[0][1],
                                              show_label=False, elem_classes="ls-fmt")
                        export_warn = gr.HTML("", visible=False, elem_classes="ls-modal-warn")
                        with gr.Row(elem_classes="ls-modal-actions"):
                            export_cancel = gr.Button("取消", elem_classes="tbtn", scale=0)
                            export_ok = gr.Button("開始匯出", elem_classes="tbtn accent", scale=0)

                # ---- 刪除專案對話框 ----
                with gr.Column(visible=False, elem_classes="ls-modal-backdrop") as del_modal:
                    with gr.Column(elem_classes="ls-modal-box"):
                        gr.HTML('<div class="ls-modal-title">🗑 刪除專案</div>')
                        # 用 Radio 列出專案（避開「visible 切換時 Dropdown 不掛載」的 Gradio 雷）
                        del_pick = gr.Radio(project_choices(), value=None,
                                            label="選擇要刪除的專案", elem_classes="ls-fmt")
                        gr.HTML('⚠️ 刪除後<b>無法復原</b>：該專案的基本資料、標記與編輯影像'
                                '（<code>projects/&lt;slug&gt;/</code> 整個資料夾）會被永久移除。',
                                elem_classes="ls-modal-warn", visible=True)
                        with gr.Row(elem_classes="ls-modal-actions"):
                            del_cancel = gr.Button("取消", elem_classes="tbtn", scale=0)
                            del_ok = gr.Button("🗑 確定刪除", elem_classes="tbtn danger-solid", scale=0)

            with gr.Tab("🏋 訓練", id="train"):
                (train_proj_dd, train_recipe_radio,
                 train_infer_btn, train_last_run, train_btn_ref) = train.build_train_tab()
            with gr.Tab("🔍 推論", id="infer"):
                (infer_proj_dd, infer_run_dd,
                 infer_pick_btn, infer_batch_btn, infer_single_btn,
                 infer_save_btn, infer_csv_btn) = infer.build_infer_tab()
        gr.HTML(FOOTER_HTML)

        # ---- 輸出組 ----
        FORM = [name_tb, disc_tb, task_radio, author_tb, ver_tb, lic_tb, desc_tb, cls_tb]
        R4 = [page_radio, gallery, detail_html, count_md]
        # PV 末尾含 assign_dd：凡是用 *PV 的事件都會自動同步『指派分類』下拉的
        # 選項/值/啟用狀態（preview_bundle 第 4 個回傳值 = assign_update）
        PV = [preview_img, data_box, preview_meta, assign_dd]
        BRIDGE_OUT = [S, page_radio, gallery, detail_html, count_md,
                      preview_img, data_box, preview_meta, cls_tb, assign_dd, info_md]

        # ---- 事件 ----
        proj_dd.change(on_proj_change, [proj_dd, S], [S, *FORM, *R4, info_md, *PV])
        new_btn.click(on_new_project, [S], [S, *FORM, *R4, info_md, *PV, proj_dd])
        save_card_btn.click(on_save_card, [*FORM, S], [S, proj_dd, info_md, *R4, assign_dd])
        task_radio.change(on_task_change, [task_radio, S], [S, *R4, assign_dd, info_md])

        folder_btn.click(on_pick_folder, [S], [S, *R4, *PV, info_md])
        view_dd.change(on_view, [view_dd, S], [S, *R4])
        page_radio.change(on_page, [page_radio, S], [S, *R4, *PV])
        assign_dd.input(on_assign, [assign_dd, S], [S, *R4, *PV, info_md])
        gallery.select(on_gallery_select, [S], [S, *PV])
        bridge_btn.click(on_bridge, [bridge, S], BRIDGE_OUT).then(None, None, None, js=POST_BRIDGE_JS)

        export_btn.click(on_export_open, [S], [export_modal, export_fmt, export_warn, export_ok])
        export_fmt.change(on_export_fmt_change, [export_fmt, S], [export_warn, export_ok])
        export_cancel.click(lambda: gr.update(visible=False), None, [export_modal])
        export_ok.click(on_export_confirm, [export_fmt, S],
                        [export_modal, export_file, export_warn, export_ok])

        start_train_btn.click(on_start_train, [S],
                              [tabs, train_proj_dd, train_recipe_radio])
        train_infer_btn.click(on_goto_infer, [train_last_run],
                              [tabs, infer_proj_dd, infer_run_dd])

        del_btn.click(on_del_open, [S], [del_modal, del_pick])
        del_cancel.click(lambda: gr.update(visible=False), None, [del_modal])
        del_ok.click(on_del_confirm, [del_pick, S],
                     [del_modal, proj_dd, S, *FORM, *R4, info_md, *PV])

        # 頁面載入：重讀磁碟刷新專案下拉（避免 F5 後新建專案消失）
        demo.load(on_page_projects, [S], [proj_dd, del_pick])
        # 初始渲染：讓 gallery / detail 都先掛載，再依檢視模式收起其一
        demo.load(lambda s: render4(s), [S], R4)
        demo.load(None, None, None, js=INIT_JS)
        if store.PUBLIC:
            # 訪客把「本機操作」按鈕虛化停用（唯讀）：選資料夾 / 存檔 / 新增 / 刪除 / 匯出 / 訓練 / 推論。
            # 站主一登入即自動開本地模式 → 這些鈕可用；取消勾選 = 預覽訪客（再度虛化+後端擋）。
            # 後端 auth.public_locked / guard_owner 為權威把關，這裡是 UI 提示（虛化勝過隱藏）。
            OWNER_BTNS = [folder_btn, save_card_btn, new_btn, del_btn, export_btn, start_train_btn,
                          train_btn_ref, infer_pick_btn, infer_batch_btn, infer_single_btn,
                          infer_save_btn, infer_csv_btn]

            def _gate_btns(request: gr.Request):
                # 載入時：站主 → 鈕可用（identity_bar 已自動開本地模式）；訪客 → 虛化。
                return auth.owner_button_gate(len(OWNER_BTNS), request)

            def _toggle_local(on, request: gr.Request):
                # 站主勾/取消「本地模式」：勾=可完整操作；取消=預覽訪客唯讀（鈕虛化+後端擋）。
                owner = auth.is_owner(request)
                active = bool(on) and owner
                auth.set_local_mode(active)
                # 只在「進入預覽（關閉）」時提示；開啟時 checkbox 勾起本身即自明，避免每次載入都跳 toast。
                if owner and not active:
                    gr.Info("👁 預覽訪客唯讀中。勾回「本地模式」即可恢復操作。")
                en = gr.update(interactive=active)
                return [en] * len(OWNER_BTNS)

            # 公開站：判定身分 → 身分列 + 站主自動開本地模式（checkbox 勾起可見）。
            demo.load(auth.identity_bar, None, [identity_html, local_chk])
            demo.load(_gate_btns, None, OWNER_BTNS)
            local_chk.change(_toggle_local, local_chk, OWNER_BTNS)
            if auth.AUTH_ENABLED:
                demo.load(None, None, None, js=auth.AUTH_JS)   # 站內 Google 登入（登入/登出鈕 + GSI）
    return demo


def launch_app(prevent_thread_lock=False, inbrowser=False, server_port=None, **ov):
    env_paths = os.environ.get("SCOPELAB_ALLOWED_PATHS", "").strip()
    if env_paths:
        allowed = [p.strip() for p in env_paths.split(";") if p.strip() and Path(p.strip()).exists()]
    else:
        allowed = [f"{d}:\\" for d in "CDEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{d}:\\").exists()]
    # 確保「下載桌面版」按鈕能透過 /gradio_api/file= 取得安裝檔（不論 allowed env 怎麼設）
    _dist = str(Path(__file__).resolve().parent / "packaging" / "dist")
    if Path(_dist).exists() and _dist not in allowed:
        allowed.append(_dist)
    _u, _p = os.environ.get("SCOPELAB_AUTH_USER"), os.environ.get("SCOPELAB_AUTH_PASS")
    basic_auth = (_u, _p) if (_u and _p) else None
    port = int(server_port or os.environ.get("SCOPELAB_PORT", 7861) or 7861)
    print(f"[ScopeLab] {len(store.list_projects())} projects | allowed_paths={allowed} | "
          f"basic_auth={'on' if basic_auth else 'off'} | "
          f"mode={'PUBLIC' if store.PUBLIC else 'local'} | "
          f"google-login={'ON' if auth.AUTH_ENABLED else 'off'}")
    demo = build()
    if auth.AUTH_ENABLED:
        # 站內 Google 登入：掛自訂 /auth/* 路由到 Gradio 底層 FastAPI，再用 uvicorn 起（127.0.0.1，經 tunnel）。
        import uvicorn
        from fastapi import FastAPI
        fastapi_app = FastAPI()
        auth.add_auth_routes(fastapi_app)
        mounted = gr.mount_gradio_app(fastapi_app, demo, path="/", allowed_paths=allowed,
                                      theme=THEME, css=CSS, show_error=True)
        server = uvicorn.Server(uvicorn.Config(mounted, host="127.0.0.1", port=port, log_level="warning"))
        if prevent_thread_lock:
            import threading
            threading.Thread(target=server.run, daemon=True).start()
            return mounted
        server.run()
        return mounted
    kw = dict(theme=THEME, css=CSS, allowed_paths=allowed,
              prevent_thread_lock=prevent_thread_lock, inbrowser=inbrowser)
    if basic_auth:
        kw["auth"] = basic_auth
    if server_port:
        kw["server_port"] = server_port
    kw.update(ov)
    return demo.launch(**kw)


if __name__ == "__main__":
    # 專用埠 7861，避開 ClinScope 的 7860；可用 SCOPELAB_PORT 覆寫
    launch_app(server_port=int(os.environ.get("SCOPELAB_PORT", "7861")))
