"""run_desktop.py — ScopeLab 桌面啟動器（離線安裝版進入點）。

在背景執行緒起 Gradio 伺服器（127.0.0.1:PORT），等埠就緒後用 pywebview 開一個原生
桌面視窗指向它；若沒裝 pywebview / 開窗失敗，退回用系統預設瀏覽器開。

打包後（PyInstaller）以本檔為進入點 → 使用者雙擊 exe 即見桌面視窗，不需懂瀏覽器/網址。
使用者資料（projects/exports）導到 %LOCALAPPDATA%\\ScopeLab；首次啟動離線重生示範專案
（純 PIL、寫入正確本機路徑，避免打包帶絕對路徑的 demo 到別人機器壞掉）。

環境變數：
  SCOPELAB_PORT          伺服器埠（預設 7861）
  SCOPELAB_DATA_DIR      使用者資料根（本啟動器自動設為 %LOCALAPPDATA%\\ScopeLab）
  SCOPELAB_DESKTOP_SMOKE =1 時只驗「伺服器起得來、HTTP 回 200」就結束，不開視窗（供無頭測試）
  SCOPELAB_SMOKE_MARKER  smoke 測試結果寫出路徑（預設 cwd/_smoke_marker.json）
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 讓 import app / from src import ... / import make_samples 可運作（dev 與 PyInstaller 解包後皆適用）
for _p in (str(ROOT), str(ROOT / "src"), str(ROOT / "samples")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# windowed 凍結 exe 可能沒有 stdout/stderr（None）→ print 會炸，補上吞掉用的替身
for _name in ("stdout", "stderr"):
    if getattr(sys, _name, None) is None:
        try:
            setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))
        except Exception:
            pass


def _data_dir() -> Path:
    """使用者資料根：%LOCALAPPDATA%\\ScopeLab（無則退回家目錄）。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home())
    d = Path(base) / "ScopeLab"
    d.mkdir(parents=True, exist_ok=True)
    return d


DATA_DIR = _data_dir()
os.environ.setdefault("SCOPELAB_DATA_DIR", str(DATA_DIR))

PORT = int(os.environ.get("SCOPELAB_PORT", "7861"))
URL = f"http://127.0.0.1:{PORT}"
SMOKE = os.environ.get("SCOPELAB_DESKTOP_SMOKE") == "1"


def _seed_demos() -> None:
    """首次啟動：使用者資料夾沒有任何專案時，用 make_samples 離線重生示範專案。"""
    projects = DATA_DIR / "projects"
    try:
        if projects.exists() and any(projects.iterdir()):
            return
        os.environ["SCOPELAB_DATA_DIR"] = str(DATA_DIR)
        import make_samples
        make_samples.make_cls()
        make_samples.make_det()
        print(f"[desktop] 已生成示範專案 → {projects}")
    except Exception as e:   # noqa: BLE001
        print(f"[desktop] 示範專案種子略過：{e}")


def _start_server() -> None:
    """在本執行緒啟動 Gradio（非阻塞）；伺服器自身另起 daemon thread 服務請求。"""
    import app
    app.launch_app(prevent_thread_lock=True, server_port=PORT, inbrowser=False, quiet=True)


def _wait_until_up(timeout: float = 120.0) -> bool:
    """輪詢直到埠可連線且 HTTP 回應；逾時回 False。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=1):
                pass
            with urllib.request.urlopen(URL, timeout=3) as r:
                if r.status < 500:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def _open_window() -> bool:
    """嘗試用 pywebview 開原生視窗（阻塞至關窗）。成功回 True；不可用回 False。"""
    try:
        import webview
    except Exception as e:   # noqa: BLE001
        print(f"[desktop] 找不到 pywebview（{e}）；改用瀏覽器。")
        return False
    try:
        webview.create_window("ScopeLab — 影像辨識標記工作台", URL,
                              width=1480, height=940, min_size=(1100, 720))
        webview.start()   # 阻塞直到使用者關閉視窗
        return True
    except Exception as e:   # noqa: BLE001
        print(f"[desktop] pywebview 開窗失敗（{e}）；改用瀏覽器。")
        return False


def main() -> None:
    if not SMOKE:
        _seed_demos()

    t = threading.Thread(target=_start_server, daemon=True)
    t.start()

    if not _wait_until_up():
        print("[desktop] 伺服器在時限內未就緒，結束。")
        sys.exit(1)
    print(f"[desktop] 伺服器就緒：{URL}")

    if SMOKE:
        # 寫出 marker 檔以驗證凍結後真的載入 app + store（windowed exe 無 stdout 時也可讀）
        try:
            import json as _json
            import app
            marker = {
                "projects": len(app.store.list_projects()),
                "data_dir": str(DATA_DIR),
                "data_root": str(app.store.DATA_ROOT),
                "has_download_btn_code": hasattr(app, "SETUP_DL_URL"),
            }
        except Exception as e:   # noqa: BLE001
            marker = {"error": repr(e)}
        out = os.environ.get("SCOPELAB_SMOKE_MARKER") or str(Path.cwd() / "_smoke_marker.json")
        try:
            Path(out).write_text(__import__("json").dumps(marker, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        print("SMOKE OK", marker)
        os._exit(0)   # 跳過 Gradio daemon thread 的收尾，直接乾淨結束

    if not _open_window():
        # 退路：開系統瀏覽器，主執行緒掛住讓伺服器持續服務
        import webbrowser
        webbrowser.open(URL)
        print(f"[desktop] 已用瀏覽器開啟 {URL}；關閉此視窗即停止伺服器。")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
