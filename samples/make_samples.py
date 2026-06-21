"""產生示範用幾何形狀影像 + 一個示範標註專案（純 UI 展示，非真實領域資料）。
執行：  python samples/make_samples.py
"""
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples" / "shapes"
OUT.mkdir(parents=True, exist_ok=True)

SHAPES = ["circle", "square", "triangle"]
ZH = {"circle": "圓形", "square": "方形", "triangle": "三角形"}
COLORS = [(34, 86, 143), (255, 138, 101), (129, 199, 132), (186, 104, 200), (77, 208, 225)]
random.seed(7)


def draw(path, shape, idx):
    W = H = 320
    im = Image.new("RGB", (W, H), (244, 248, 253))
    d = ImageDraw.Draw(im)
    col = random.choice(COLORS)
    m = random.randint(50, 80)
    box = [m, m, W - m, H - m]
    if shape == "circle":
        d.ellipse(box, fill=col)
    elif shape == "square":
        d.rectangle(box, fill=col)
    else:
        d.polygon([(W // 2, m), (m, H - m), (W - m, H - m)], fill=col)
    im.save(path)


files = []
for shp in SHAPES:
    for i in range(6):
        p = OUT / f"{shp}_{i:02d}.png"
        draw(p, shp, i)
        files.append((str(p), shp))

# 示範專案：分類，部分先標好、部分留 Null
proj = ROOT / "projects" / "shapes_demo"
proj.mkdir(parents=True, exist_ok=True)
card = {
    "slug": "shapes_demo", "name": "形狀分類示範", "discipline": "示範",
    "task": "cls", "classes": [ZH[s] for s in SHAPES],
    "author": "LabelScope", "version": "0.1.0", "license": "CC0",
    "description": "內建示範資料：幾何形狀三分類，用來展示標註流程與全螢幕編輯器。",
    "source_folder": str(OUT), "created": "2026-06-22", "updated": "2026-06-22",
}
(proj / "card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")

# 標好 2/3，剩 1/3 留待分類
labels = {}
for i, (p, shp) in enumerate(files):
    if i % 3 != 0:
        labels[p] = ZH[shp]
(proj / "labels.json").write_text(
    json.dumps({"labels": labels, "edits": {}}, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"已產生 {len(files)} 張示範影像 → {OUT}")
print(f"示範專案 → {proj}  （已標 {len(labels)} 張，留 {len(files)-len(labels)} 張未分類）")
