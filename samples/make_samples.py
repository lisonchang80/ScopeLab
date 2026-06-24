"""產生示範用幾何形狀影像 + 兩個示範標註專案（純 UI 展示，非真實領域資料）。

  分類示範 shapes_demo     ：samples/shapes/      每張一個置中形狀（單標籤）
  偵測示範 shapes_det_demo ：samples/shapes_det/  每張 2–5 個散佈形狀（多框，符合偵測真實樣態）

執行：  python samples/make_samples.py
"""
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent

SHAPES = ["circle", "square", "triangle"]
ZH = {"circle": "圓形", "square": "方形", "triangle": "三角形"}
COLORS = [(34, 86, 143), (255, 138, 101), (129, 199, 132), (186, 104, 200), (77, 208, 225)]


def _draw_shape(d, shape, box, col):
    """在像素 box=[x0,y0,x1,y1] 內畫一個實心形狀。"""
    x0, y0, x1, y1 = box
    if shape == "circle":
        d.ellipse(box, fill=col)
    elif shape == "square":
        d.rectangle(box, fill=col)
    else:
        d.polygon([((x0 + x1) // 2, y0), (x0, y1), (x1, y1)], fill=col)


# ---- 分類示範：每張一個置中形狀（保持與初版位元一致：先 color 再 margin）----
def make_cls():
    out = ROOT / "samples" / "shapes"
    out.mkdir(parents=True, exist_ok=True)
    random.seed(7)
    files = []
    for shp in SHAPES:
        for i in range(6):
            W = H = 320
            im = Image.new("RGB", (W, H), (244, 248, 253))
            d = ImageDraw.Draw(im)
            col = random.choice(COLORS)
            m = random.randint(50, 80)
            _draw_shape(d, shp, [m, m, W - m, H - m], col)
            p = out / f"{shp}_{i:02d}.png"
            im.save(p)
            files.append((str(p), shp))

    proj = ROOT / "projects" / "shapes_demo"
    proj.mkdir(parents=True, exist_ok=True)
    card = {
        "slug": "shapes_demo", "name": "形狀分類示範", "discipline": "示範",
        "task": "cls", "classes": [ZH[s] for s in SHAPES],
        "author": "ScopeLab", "version": "0.1.0", "license": "CC0",
        "description": "內建示範資料：幾何形狀三分類，用來展示標註流程與全螢幕編輯器。",
        "source_folder": str(out), "created": "2026-06-22", "updated": "2026-06-22",
    }
    (proj / "card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    # 標好 2/3，剩 1/3 留待分類
    labels = {p: ZH[shp] for i, (p, shp) in enumerate(files) if i % 3 != 0}
    (proj / "labels.json").write_text(
        json.dumps({"labels": labels, "edits": {}}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[分類] {len(files)} 張 → {out}（已標 {len(labels)} / 留 {len(files)-len(labels)} 未分類）")


# ---- 偵測示範：每張 2–5 個散佈形狀，各自一個框 ----------------------------
def _draw_scene(path, n_target):
    """畫一張多形狀場景，回傳每個形狀的正規化框 [{cls,x,y,w,h}]（彼此不重疊）。"""
    W, H = 480, 360
    im = Image.new("RGB", (W, H), (244, 248, 253))
    d = ImageDraw.Draw(im)
    boxes, placed, tries = [], [], 0
    while len(boxes) < n_target and tries < 240:
        tries += 1
        shp = random.choice(SHAPES)
        sw = random.randint(58, 104)
        sh = sw if shp != "triangle" else random.randint(58, 104)
        x0 = random.randint(10, W - sw - 10)
        y0 = random.randint(10, H - sh - 10)
        x1, y1 = x0 + sw, y0 + sh
        # 與已放置者保留 ≥8px 間距，避免重疊難辨
        if any(not (x1 < q[0] - 8 or x0 > q[2] + 8 or y1 < q[1] - 8 or y0 > q[3] + 8) for q in placed):
            continue
        _draw_shape(d, shp, [x0, y0, x1, y1], random.choice(COLORS))
        placed.append((x0, y0, x1, y1))
        boxes.append({"cls": ZH[shp],
                      "x": round(x0 / W, 6), "y": round(y0 / H, 6),
                      "w": round(sw / W, 6), "h": round(sh / H, 6)})
    im.save(path)
    return boxes


def make_det():
    out = ROOT / "samples" / "shapes_det"
    out.mkdir(parents=True, exist_ok=True)
    random.seed(11)
    files, labels = [], {}
    for i in range(15):
        p = out / f"scene_{i:02d}.png"
        boxes = _draw_scene(p, random.randint(2, 5))
        files.append(str(p))
        if i % 3 != 0:            # 預先標好 2/3 的框，留 1/3 未標（展示畫框流程）
            labels[str(p)] = boxes

    proj = ROOT / "projects" / "shapes_det_demo"
    proj.mkdir(parents=True, exist_ok=True)
    card = {
        "slug": "shapes_det_demo", "name": "形狀偵測示範", "discipline": "示範",
        "task": "det", "classes": [ZH[s] for s in SHAPES],
        "author": "ScopeLab", "version": "0.1.0", "license": "CC0",
        "description": "內建偵測示範：每張影像散佈多個形狀，逐一畫框標出，"
                       "展示全螢幕標註器與 YOLO/COCO 多框匯出。",
        "source_folder": str(out), "created": "2026-06-22", "updated": "2026-06-24",
    }
    (proj / "card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    (proj / "labels.json").write_text(
        json.dumps({"labels": labels, "edits": {}}, ensure_ascii=False, indent=2), encoding="utf-8")
    nb = sum(len(v) for v in labels.values())
    print(f"[偵測] {len(files)} 張 → {out}（已標 {len(labels)} 張 / 共 {nb} 個框，留 {len(files)-len(labels)} 張未標）")


if __name__ == "__main__":
    make_cls()
    make_det()
