"""ScopeLab — 匯出。

分類（cls）
  cls_folder  對接 ClinScope/MatScope：ImageFolder  exports/<slug>/<類別>/*.jpg  + card.json
  cls_zip     通用 ZIP：同結構打包成 .zip

偵測（det）
  det_yolo    對接 ClinScope/MatScope：images/ + labels/<name>.txt(YOLO) + data.yaml + card.json
  det_coco    通用：images/ + annotations.json（COCO）
  det_zip     通用 ZIP：YOLO 結構打包成 .zip

座標：labels 內 box 為正規化 x,y,w,h（左上角+寬高）。YOLO 需 cx,cy,w,h；COCO 需像素 x,y,w,h。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
from pathlib import Path

import zipfile

import yaml
from PIL import Image

from . import store
from .imaging import display_path

EXPORTS = store.DATA_ROOT / "exports"

# 每個匯出格式落地的資料夾後綴（同一 tag 的格式共用一個資料夾）
FMT_TAG = {
    "cls_folder": "imagefolder",
    "cls_zip": "imagefolder",
    "det_yolo": "yolo",
    "det_zip": "yolo",
    "det_coco": "coco",
}


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", str(name)).strip() or "_"


def _base(slug: str, name: str | None = None) -> str:
    """匯出資料夾的基底名：優先用可讀的專案名稱（_safe 保留中文），退回 slug。"""
    b = _safe(name) if name else ""
    return b if b and b != "_" else (slug or "export")


def _owner_slug(d: Path) -> str | None:
    """既有匯出（資料夾或同名 .zip）裡 card.json 記的專案 slug；沒有或讀不到回 None。"""
    try:
        if d.is_dir():
            cp = d / "card.json"
            raw = cp.read_text(encoding="utf-8") if cp.is_file() else None
        elif Path(str(d) + ".zip").is_file():
            with zipfile.ZipFile(str(d) + ".zip") as z:
                raw = z.read("card.json").decode("utf-8") if "card.json" in z.namelist() else None
        else:
            raw = None
        return (json.loads(raw).get("slug") or None) if raw else None
    except Exception:
        return None


def _named_dest(slug: str, fmt: str, name: str | None) -> Path | None:
    tag = FMT_TAG.get(fmt)
    return (EXPORTS / f"{_base(slug, name)}__{tag}") if tag else None


def other_owner(slug: str, fmt: str, name: str | None = None) -> str | None:
    """以名稱命名的匯出夾若屬於另一個專案（card.json 的 slug 不同），回那個 slug；否則 None（審查 SL-9）。"""
    d = _named_dest(slug, fmt, name)
    owner = _owner_slug(d) if d is not None else None
    return owner if (owner and slug and owner != slug) else None


def dest_for(slug: str, fmt: str, name: str | None = None) -> Path | None:
    """該 (slug, 格式) 預期輸出的資料夾路徑（不建立）。未知格式回 None。

    預設用可讀的專案名稱 <名稱>__<格式>；同名資料夾若是另一個專案（slug 不同，store.unique_slug 允許同名
    專案共存）的匯出，改用 <名稱>__<slug>__<格式>，不覆蓋別人的匯出（審查 SL-9）。同一個專案維持原名。"""
    d = _named_dest(slug, fmt, name)
    if d is None:
        return None
    if other_owner(slug, fmt, name):
        return EXPORTS / f"{_base(slug, name)}__{_safe(slug)}__{FMT_TAG[fmt]}"
    return d


def already_exists(slug: str, fmt: str, name: str | None = None) -> bool:
    """該格式是否已有舊匯出（資料夾或對應 .zip）。"""
    d = dest_for(slug, fmt, name)
    if d is None:
        return False
    if d.exists():
        return True
    if fmt.endswith("_zip") and Path(str(d) + ".zip").exists():
        return True
    return False


def _force_rmtree(path: Path):
    """健壯刪除：清掉唯讀旗標後重試（Windows 常見）。"""
    def _onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass
    for _ in range(3):
        if not path.exists():
            return
        try:
            shutil.rmtree(path, onexc=lambda f, p, e: _onerror(f, p, e))
        except TypeError:  # Python < 3.12 沒有 onexc
            shutil.rmtree(path, onerror=_onerror)


def _fresh(out: Path) -> Path:
    EXPORTS.mkdir(exist_ok=True)
    if out.exists():
        _force_rmtree(out)
        if out.exists():
            # Windows：資料夾被占用（在檔案總管開著／檔案被其他程式鎖住）
            raise RuntimeError(
                f"無法覆蓋舊匯出資料夾：{out}\n"
                "可能它正在檔案總管中開啟，或裡面有檔案被其他程式占用。"
                "請關閉後再試一次。")
    out.mkdir(parents=True, exist_ok=True)
    return out


def _write_card(dest: Path, card: dict, stats: dict):
    payload = dict(card)
    payload["export_stats"] = stats
    (dest / "card.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---- classification ------------------------------------------------------

def export_cls(slug, card, files, labels, edits, dest: Path, progress=None):
    classes = card.get("classes", [])
    per = {c: 0 for c in classes}
    n = 0
    total = len(files) or 1
    for i, src in enumerate(files):
        if progress:
            progress(i / total, desc=f"複製影像 {i + 1}/{len(files)}")
        cls = labels.get(src)
        if not isinstance(cls, str) or cls not in classes:
            continue
        cdir = dest / _safe(cls)
        cdir.mkdir(parents=True, exist_ok=True)
        srcp = display_path(src, edits)
        dst = cdir / Path(src).name
        i = 2
        while dst.exists():
            dst = cdir / f"{Path(src).stem}_{i}{Path(src).suffix}"
            i += 1
        shutil.copy2(srcp, dst)
        per[cls] = per.get(cls, 0) + 1
        n += 1
    return {"images": n, "per_class": per, "classes": classes}


# ---- detection -----------------------------------------------------------

def export_det_yolo(slug, card, files, labels, edits, dest: Path, progress=None):
    classes = card.get("classes", [])
    cidx = {c: i for i, c in enumerate(classes)}
    img_dir, lbl_dir = dest / "images", dest / "labels"
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)
    n_img = n_box = 0
    total = len(files) or 1
    for idx, src in enumerate(files):
        if progress:
            progress(idx / total, desc=f"處理影像 {idx + 1}/{len(files)}")
        boxes = labels.get(src)
        if not isinstance(boxes, list) or not boxes:
            continue
        srcp = display_path(src, edits)
        shutil.copy2(srcp, img_dir / Path(src).name)
        lines = []
        for b in boxes:
            if b.get("cls") not in cidx:
                continue
            cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
            lines.append(f"{cidx[b['cls']]} {cx:.6f} {cy:.6f} {b['w']:.6f} {b['h']:.6f}")
            n_box += 1
        (lbl_dir / (Path(src).stem + ".txt")).write_text("\n".join(lines), encoding="utf-8")
        n_img += 1
    (dest / "data.yaml").write_text(yaml.safe_dump({
        "path": ".", "train": "images", "val": "images",
        "nc": len(classes), "names": {i: c for i, c in enumerate(classes)},
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"images": n_img, "boxes": n_box, "classes": classes}


def export_det_coco(slug, card, files, labels, edits, dest: Path, progress=None):
    classes = card.get("classes", [])
    img_dir = dest / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    cats = [{"id": i + 1, "name": c} for i, c in enumerate(classes)]
    cid = {c: i + 1 for i, c in enumerate(classes)}
    images, annos = [], []
    aid = 1
    total = len(files) or 1
    for idx, src in enumerate(files):
        if progress:
            progress(idx / total, desc=f"處理影像 {idx + 1}/{len(files)}")
        boxes = labels.get(src)
        if not isinstance(boxes, list) or not boxes:
            continue
        srcp = display_path(src, edits)
        try:
            W, H = Image.open(srcp).size
        except Exception:
            continue
        iid = len(images) + 1
        shutil.copy2(srcp, img_dir / Path(src).name)
        images.append({"id": iid, "file_name": Path(src).name, "width": W, "height": H})
        for b in boxes:
            if b.get("cls") not in cid:
                continue
            x, y, w, h = b["x"] * W, b["y"] * H, b["w"] * W, b["h"] * H
            annos.append({"id": aid, "image_id": iid, "category_id": cid[b["cls"]],
                          "bbox": [round(x, 1), round(y, 1), round(w, 1), round(h, 1)],
                          "area": round(w * h, 1), "iscrowd": 0})
            aid += 1
    (dest / "annotations.json").write_text(json.dumps(
        {"images": images, "annotations": annos, "categories": cats},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return {"images": len(images), "boxes": len(annos), "classes": classes}


# ---- entry ---------------------------------------------------------------

def run(slug, card, files, labels, edits, fmt, progress=None) -> tuple[str, dict, bool]:
    """回傳 (結果路徑, 統計, 是否為 zip)。"""
    name = card.get("name")
    if fmt not in FMT_TAG:
        raise ValueError(f"未知匯出格式：{fmt}")
    target = dest_for(slug, fmt, name)          # 與 already_exists／警告文字同一個目的地（審查 SL-9）
    if fmt == "cls_folder":
        dest = _fresh(target)
        stats = export_cls(slug, card, files, labels, edits, dest, progress)
        _write_card(dest, card, stats)
        return str(dest), stats, False
    if fmt == "cls_zip":
        dest = _fresh(target)
        stats = export_cls(slug, card, files, labels, edits, dest, progress)
        _write_card(dest, card, stats)
        if progress:
            progress(0.95, desc="壓縮 ZIP…")
        zp = shutil.make_archive(str(dest), "zip", dest)
        return zp, stats, True
    if fmt in ("det_yolo", "det_zip"):
        dest = _fresh(target)
        stats = export_det_yolo(slug, card, files, labels, edits, dest, progress)
        _write_card(dest, card, stats)
        if fmt == "det_zip":
            if progress:
                progress(0.95, desc="壓縮 ZIP…")
            return shutil.make_archive(str(dest), "zip", dest), stats, True
        return str(dest), stats, False
    if fmt == "det_coco":
        dest = _fresh(target)
        stats = export_det_coco(slug, card, files, labels, edits, dest, progress)
        _write_card(dest, card, stats)
        return str(dest), stats, False
    raise ValueError(f"未知匯出格式：{fmt}")
