# scopelab.spec — PyInstaller 規格（桌面安裝版）。
#
# ⚠️ 必須從「乾淨 CPU-only venv」建置（packaging/requirements-runtime.txt），不要用 dev 的
#    cu124 venv，否則會把 ~2.5GB CUDA runtime 打進安裝包（且文書機用不到）。走 build_exe.ps1。
#
#    pyinstaller --noconfirm --clean packaging/scopelab.spec \
#        --distpath packaging/dist --workpath packaging/build
#
# 產物：packaging/dist/ScopeLab/ScopeLab.exe（onedir）。
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent          # SPECPATH = packaging/ → ROOT = repo 根
SRC = str(ROOT / "src")
for _p in (str(ROOT), SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

datas, binaries, hiddenimports = [], [], []


def _add(pkg):
    """collect_all 一個套件（資料+動態庫+子模組）；失敗只警告不中斷。"""
    try:
        d, b, h = collect_all(pkg)
        datas.extend(d); binaries.extend(b); hiddenimports.extend(h)
        print(f"[spec] collected {pkg}: +{len(d)} datas +{len(b)} bins +{len(h)} hidden")
    except Exception as e:                       # noqa: BLE001
        print(f"[spec] collect_all({pkg}) 跳過：{e}")


# 有執行期模板/設定/資料、PyInstaller 靜態分析常漏掉的套件 → 全收
for _pkg in ("gradio", "gradio_client", "safehttpx", "groovy",
             "ultralytics", "torch", "torchvision", "webview"):
    _add(_pkg)

# sklearn / scipy 子模組（分類 split + 指標）
hiddenimports += collect_submodules("sklearn")
hiddenimports += collect_submodules("scipy")

# 專案進入點 + 套件樹 + 示範重生腳本（首次啟動離線生成 demo）。
# 比照 ClinScope：專案自有 .py 以 data 檔隨附，run_desktop 把 ROOT/SRC 加入 sys.path 後 import，
# 避免 PyInstaller 漏掉「函式內 lazy import」的專案子模組。
datas += [
    (str(ROOT / "app.py"), "."),
    (str(ROOT / "samples" / "make_samples.py"), "samples"),
]
for _py in (ROOT / "src").rglob("*.py"):
    datas.append((str(_py), str(_py.parent.relative_to(ROOT))))

# 離線 YOLO 基礎權重（det 訓練免連網下載）
for _w in ("yolov8s.pt", "yolo26n.pt"):
    _wp = ROOT / _w
    if _wp.exists():
        datas.append((str(_wp), "."))

# 額外隱藏匯入：進入點 / pywebview Windows 後端 / tkinter 原生資料夾對話框
hiddenimports += [
    "app", "make_samples",
    "tkinter", "tkinter.filedialog",
    "webview.platforms.winforms", "webview.platforms.edgechromium",
    "clr", "PIL._tkinter_finder",
]

# gradio 必須保留原始 .py（它執行期會讀自身原始碼/模板，純 .pyc 會壞）
module_collection_mode = {"gradio": "py", "gradio_client": "py"}

a = Analysis(
    [str(ROOT / "packaging" / "run_desktop.py")],
    pathex=[str(ROOT), SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["kaggle", "gdown", "datasets", "pyarrow",          # 僅 data prep 用
              "tensorflow", "flax", "jax",                        # 不用的 DL 後端
              "transformers", "timm", "sahi"],                    # ClinScope 專屬，ScopeLab 不需
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ScopeLab",
    console=False,            # 桌面 app：不開黑窗（出錯走 run_desktop 的瀏覽器退路）
    icon=None,
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="ScopeLab",
)
