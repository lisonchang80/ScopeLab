"""ScopeLab — 專案／標註持久層。

每個標註專案存在 projects/<slug>/：
  card.json    基本資料（名稱、科別、任務 cls/det、類別清單、作者…）
  labels.json  標註內容 + 影像編輯覆寫對映
  edited/      影像編輯器存回的編輯版影像

分類（cls）：labels[abs_path] = "類別名"           （未分類 = 不在 dict / Null）
偵測（det）：labels[abs_path] = [ {cls,x,y,w,h}, ... ]  座標皆正規化 0–1（左上角 + 寬高）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 資料根目錄：桌面安裝版把使用者資料導到 %LOCALAPPDATA%\ScopeLab（免寫進安裝夾、重裝不丟）。
# 開發 / 公開站不設此環境變數 → 沿用 repo 根（既有 projects/ 照常）。
_DATA = os.environ.get("SCOPELAB_DATA_DIR", "").strip()
DATA_ROOT = Path(_DATA) if _DATA else ROOT
PROJECTS = DATA_ROOT / "projects"
PROJECTS.mkdir(parents=True, exist_ok=True)

# 公開展示模式：公開站設 SCOPELAB_PUBLIC=1 → 關掉「訓練執行」與「伺服器端選資料夾」，
# 只剩瀏覽範例 / 看 UI / 下載桌面版（避免遠端訪客用到本機 GPU/硬碟）。桌面版/開發不設 → 全功能。
PUBLIC = os.environ.get("SCOPELAB_PUBLIC", "").strip().lower() in ("1", "true", "yes")
PUBLIC_MSG = "公開展示模式：選資料夾與訓練請下載「桌面版」在自己電腦使用（本站僅供瀏覽範例與下載）。"

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
TASKS = {"cls": "分類", "det": "偵測"}

# 標記頁籤的特殊分頁鍵
TOTAL = "📋 總表"
NULL = "Null"          # cls：未分類
DONE = "✅ 已標記"      # det：含框
TODO = "◻ 未標記"      # det：無框
ADDCLASS = "＋"         # 新增類別分頁


def today() -> str:
    return date.today().isoformat()


def slugify(name: str) -> str:
    """名稱 → 檔名安全的 slug，盡量保留可讀性。
    保留中英數字（含 CJK 等 Unicode 文字），空白與其餘標點/符號轉底線；
    全空或純符號才退回 'proj'。Windows/NTFS 允許中文資料夾名稱。"""
    s = re.sub(r"[^\w]+", "_", (name or "").strip(), flags=re.UNICODE).strip("_")
    return s or "proj"


def unique_slug(name: str) -> str:
    base = slugify(name)
    slug, i = base, 2
    while (PROJECTS / slug).exists():
        slug, i = f"{base}_{i}", i + 1
    return slug


def _assert_safe_slug(slug: str) -> None:
    """縱深防禦：slug 一律是 slugify 產物（只含 \\w 與 CJK，無分隔符）。拒絕任何帶路徑
    分隔符 / 磁碟代號 / .. 段 / 絕對路徑的值，避免 delete/save 等直接吃到外部傳入的 slug
    而逸出 projects/。"""
    s = str(slug or "")
    if not s:
        return
    if (any(sep in s for sep in ("/", "\\")) or ":" in s
            or ".." in s.split("/") or ".." in s.split("\\") or s in (".", "..")):
        raise ValueError(f"非法專案代號：{slug!r}")


def project_dir(slug: str) -> Path:
    _assert_safe_slug(slug)
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
    if not slug:                 # 新專案 / 未存檔：slug 為 None 或 ""
        return None
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
    """刪除整個專案資料夾。健壯處理 Windows 唯讀／占用，失敗則丟出明確錯誤
    （不靜默放過，呼叫端才能提示使用者）。"""
    if not slug:
        return
    d = project_dir(slug)
    if not d.exists():
        return

    def _onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    for _ in range(3):
        if not d.exists():
            return
        try:
            shutil.rmtree(d, onexc=lambda f, p, e: _onerror(f, p, e))
        except TypeError:  # Python < 3.12 沒有 onexc
            shutil.rmtree(d, onerror=_onerror)
    if d.exists():
        raise RuntimeError(
            f"無法刪除專案資料夾：{d}\n"
            "可能它正在檔案總管中開啟，或裡面有檔案被其他程式占用。請關閉後再試一次。")


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
