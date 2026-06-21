"""LabelScope — 專案／標註持久層。

每個標註專案存在 projects/<slug>/：
  card.json    基本資料（名稱、科別、任務 cls/det、類別清單、作者…）
  labels.json  標註內容 + 影像編輯覆寫對映
  edited/      影像編輯器存回的編輯版影像

分類（cls）：labels[abs_path] = "類別名"           （未分類 = 不在 dict / Null）
偵測（det）：labels[abs_path] = [ {cls,x,y,w,h}, ... ]  座標皆正規化 0–1（左上角 + 寬高）
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "projects"
PROJECTS.mkdir(exist_ok=True)

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
TASKS = {"cls": "分類", "det": "偵測"}

# 標註頁籤的特殊分頁鍵
TOTAL = "📋 總表"
NULL = "Null"          # cls：未分類
DONE = "✅ 已標註"      # det：含框
TODO = "◻ 未標註"      # det：無框
ADDCLASS = "＋"         # 新增類別分頁


def today() -> str:
    return date.today().isoformat()


def slugify(name: str) -> str:
    """名稱 → 檔名安全的 slug。中文等非 ASCII 以 romaji/拼音不易，故保留英數、其餘轉底線。
    全中文名稱會落到 'proj'，再附流水號避免相撞。"""
    norm = unicodedata.normalize("NFKD", name or "")
    ascii_only = norm.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9]+", "_", ascii_only).strip("_").lower()
    return s or "proj"


def unique_slug(name: str) -> str:
    base = slugify(name)
    slug, i = base, 2
    while (PROJECTS / slug).exists():
        slug, i = f"{base}_{i}", i + 1
    return slug


def project_dir(slug: str) -> Path:
    return PROJECTS / slug


def card_path(slug: str) -> Path:
    return project_dir(slug) / "card.json"


def labels_path(slug: str) -> Path:
    return project_dir(slug) / "labels.json"


def edited_dir(slug: str) -> Path:
    d = project_dir(slug) / "edited"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---- card ----------------------------------------------------------------

def blank_card(name="新標註專案", task="cls") -> dict:
    return {
        "slug": "", "name": name, "discipline": "", "task": task,
        "classes": [], "author": "", "version": "0.1.0",
        "license": "CC BY-NC 4.0", "description": "",
        "source_folder": "", "created": today(), "updated": today(),
    }


def list_projects() -> list[dict]:
    out = []
    for d in sorted(PROJECTS.iterdir()) if PROJECTS.exists() else []:
        cp = d / "card.json"
        if cp.is_file():
            try:
                out.append(json.loads(cp.read_text(encoding="utf-8")))
            except Exception:
                pass
    out.sort(key=lambda c: c.get("updated", ""), reverse=True)
    return out


def load_card(slug: str) -> dict | None:
    cp = card_path(slug)
    if not cp.is_file():
        return None
    card = blank_card()
    card.update(json.loads(cp.read_text(encoding="utf-8")))
    card["slug"] = slug
    return card


def save_card(card: dict) -> dict:
    if not card.get("slug"):
        card["slug"] = unique_slug(card.get("name") or "proj")
    card.setdefault("created", today())
    card["updated"] = today()
    project_dir(card["slug"]).mkdir(parents=True, exist_ok=True)
    card_path(card["slug"]).write_text(
        json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    return card


def delete_project(slug: str) -> None:
    import shutil
    d = project_dir(slug)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


# ---- labels --------------------------------------------------------------

def load_labels(slug: str) -> dict:
    lp = labels_path(slug)
    if lp.is_file():
        try:
            data = json.loads(lp.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}
    data.setdefault("labels", {})
    data.setdefault("edits", {})
    return data


def save_labels(slug: str, data: dict) -> None:
    project_dir(slug).mkdir(parents=True, exist_ok=True)
    labels_path(slug).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---- folder scan ---------------------------------------------------------

def scan_folder(path: str) -> list[str]:
    p = Path(path)
    if not p.is_dir():
        return []
    files = [str(f) for f in p.iterdir()
             if f.is_file() and f.suffix.lower() in IMG_EXT]
    files.sort(key=lambda s: s.lower())
    return files
