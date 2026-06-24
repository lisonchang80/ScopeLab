"""ScopeLab — 影像處理 helper。

- load_rgb：讀圖為 RGB ndarray（給 gr.Image 預覽）
- draw_boxes：把 det 標註框畫到影像上（預覽用）
- file_url：本機路徑 → Gradio /gradio_api/file= URL（給前端編輯器抓乾淨原圖）
- save_dataurl：編輯器存回的 PNG dataURL → 解碼存到 projects/<slug>/edited/
"""
from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path
from urllib.parse import quote

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import store

# 類別配色（依類別在清單中的索引取色）— 高彩度、彼此分明
PALETTE = [
    "#FFE14D", "#4DD0E1", "#FF8A65", "#81C784", "#BA68C8", "#F06292",
    "#64B5F6", "#FFD54F", "#A1887F", "#90A4AE", "#E57373", "#9CCC65",
    "#7986CB", "#4DB6AC", "#FF7043", "#DCE775", "#BA68C8", "#4FC3F7",
]


def class_color(classes: list[str], name: str) -> str:
    if name in classes:
        return PALETTE[classes.index(name) % len(PALETTE)]
    return "#FFE14D"


def load_rgb(path: str):
    try:
        return np.array(Image.open(path).convert("RGB"))
    except Exception:
        return None


def _font(size: int):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def draw_boxes(path: str, boxes: list[dict], classes: list[str]):
    """回傳畫好框的 RGB ndarray。boxes 為正規化 x,y,w,h（左上角+寬高）。"""
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return None
    W, H = im.size
    dr = ImageDraw.Draw(im, "RGBA")
    lw = max(2, round(min(W, H) / 300))
    ft = _font(max(13, round(min(W, H) / 45)))
    for b in boxes or []:
        x0, y0 = b["x"] * W, b["y"] * H
        x1, y1 = (b["x"] + b["w"]) * W, (b["y"] + b["h"]) * H
        col = class_color(classes, b.get("cls", ""))
        rgb = tuple(int(col[i:i + 2], 16) for i in (1, 3, 5))
        dr.rectangle([x0, y0, x1, y1], outline=rgb + (255,), width=lw)
        tag = b.get("cls", "?")
        try:
            tb = dr.textbbox((0, 0), tag, font=ft)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
        except Exception:
            tw, th = len(tag) * 8, 14
        dr.rectangle([x0, y0 - th - 6, x0 + tw + 8, y0], fill=rgb + (235,))
        dr.text((x0 + 4, y0 - th - 4), tag, fill=(11, 31, 58, 255), font=ft)
    return np.array(im)


def file_url(path: str) -> str:
    """本機路徑 → Gradio 檔案服務 URL（前端編輯器用 fetch 抓原圖）。"""
    return "/gradio_api/file=" + quote(Path(path).resolve().as_posix())


def save_dataurl(slug: str, src_path: str, dataurl: str) -> str:
    """編輯器存回 PNG dataURL → 存到 edited/，回傳新檔絕對路徑。"""
    head, _, b64 = (dataurl or "").partition(",")
    if not b64:
        raise ValueError("空的影像資料")
    raw = base64.b64decode(b64)
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    key = hashlib.md5(str(Path(src_path).resolve()).encode("utf-8")).hexdigest()[:10]
    stem = Path(src_path).stem[:40]
    out = store.edited_dir(slug) / f"{stem}__{key}.png"
    im.save(out)
    return str(out)


def display_path(src_path: str, edits: dict) -> str:
    """有編輯版就回編輯版路徑，否則回原圖。"""
    e = (edits or {}).get(src_path)
    if e and Path(e).is_file():
        return e
    return src_path
