---
name: dev-verify
description: ScopeLab 啟動/重啟與改碼後的驗證流程。觸發詞：啟動/開啟/重啟 ScopeLab、改了 app.py 要重啟、驗證 UI 改動、preview 連不上/截圖逾時。涉及 app.py、src/store.py、samples/make_samples.py，埠 7861，沿用 ClinScope venv。
---

# ScopeLab 開發 / 驗證流程

ScopeLab 是單檔 Gradio（6.17.3）app（`app.py`），沿用 ClinScope 的 venv
`D:\Claude code\ClinScope\.venv\Scripts\python.exe`，跑埠 **7861**。**Gradio 無熱重載**——改碼一定要重啟程序才生效。

## 1. 常駐啟動（給使用者實際用）

⚠️ **不要用 `preview_start` 當常駐**：preview 開的 server 驗證完會被工具收掉、使用者連不上。
要常駐必須用獨立背景程序：

```powershell
$old = (Get-NetTCPConnection -State Listen -LocalPort 7861 -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
foreach ($p in $old) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 700
$py = "D:\Claude code\ClinScope\.venv\Scripts\python.exe"
$proc = Start-Process -FilePath $py -ArgumentList "app.py" -WorkingDirectory "D:\Claude code\ScopeLab" -WindowStyle Hidden -PassThru
# 輪詢就緒
for ($i=0;$i -lt 25;$i++){ $c=Get-NetTCPConnection -State Listen -LocalPort 7861 -ErrorAction SilentlyContinue
  if($c){ try{ $r=Invoke-WebRequest "http://127.0.0.1:7861/" -UseBasicParsing -TimeoutSec 5; if($r.StatusCode -eq 200 -and $r.Content -match "ScopeLab"){"READY";break} }catch{} }; Start-Sleep -Milliseconds 800 }
```

改碼後重啟＝同一段（先 Stop-Process 該埠的 PID 再重起）。PowerShell 字串裡別用 `$var:`（會被當磁碟機），用 `${var}`。

## 2. 驗證改動（preview + DOM，**不要靠截圖/動態點擊**）

這台的 preview 瀏覽器對 ScopeLab 有幾個雷，靠它們驗證要繞開：

- **viewport 會歸零**：`window.innerWidth` 常是 0 → `30vh` 等全崩、量到的寬高是雜訊。
  **每次 `preview_start` 後先 `preview_resize` 明確指定 `width:1440, height:900`**（`preset:"desktop"` 會回 0），
  再 `location.reload()` + 等 ~1.5s 讓 Gradio textarea 自動高度重算，量到的數字才可信。
- **`preview_screenshot` 會逾時**（30s 卡住）→ 改用 `preview_eval` 量 DOM（`getBoundingClientRect`、computed style）或 `preview_inspect`。
- **動態掛載元件的合成點擊不穩**：`visible=False` 的 modal 是延遲渲染（隱藏時不在 DOM，點觸發按鈕才掛載）；
  對 modal 內按鈕 / Radio / 詳細表列 `preview_click` 常常沒生效（同一個點擊有時開有時不開）。
  - 開 modal 後**要分開一次 eval 再檢查**（click 與檢查同一個 promise 內會被 re-render 打斷，回 `{}`）。
  - 用 JS `.click()` 勾 Radio 只改 DOM checked、**不會把值送到 Gradio 後端** → 用 `preview_click` 點 input。
- **`preview_start` 用的是「工作區」launch.json**（`D:\Claude code\.claude\launch.json` 的 **`scopelab`** 設定，埠 7861），**不是 repo 內那個**；改名/換埠時這支也要同步。要用 preview 得先把外部常駐 server 停掉（埠衝突），用完再用 `Start-Process` 重啟常駐（見 §1）。
- **分頁（gr.Tabs）切換雷**：① tab 按鈕有**兩套**——一套在 `.tab-container.visually-hidden`（無障礙、點了沒用），要點**可見**那套 `.tab-container:not(.visually-hidden) button[n]` ② tab 內容**懶載入**：只有作用中的 tab 在 DOM，切過去後要等 ~2.5s 才查得到該頁元素（`.phead .ph-tag` 會同時含已渲染各頁）③ 首次 eval 常回空（DOM 還沒 settle）→ 重試一次。
- **DOM 狀態會莫名變壞**（`.tab-nav button` 一下 3 個一下 0 個）→ 別硬纏，量到關鍵數據（如版面高度鏈）就收手，靠 §3 後端驗證補。

## 3. 後端邏輯驗證（最可靠，繞開 preview 不穩）

handler 接線改動（尤其改了輸出組 `PV`/`R4`/`FORM` 或 `preview_bundle` 回傳數）→ 用 Python 直接呼叫驗證**輸入/輸出數量對齊**與邏輯：

```powershell
$py = "D:\Claude code\ClinScope\.venv\Scripts\python.exe"; $env:PYTHONIOENCODING="utf-8"
@'
import sys; sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\Claude code\ScopeLab")
import ast; ast.parse(open(r"D:\Claude code\ScopeLab\app.py",encoding="utf-8").read()); print("AST OK")
import app; from src import store
print("BUILD OK:", type(app.build()).__name__)   # ★ 最強結構檢查（見下）
# 造假 state 呼叫 handler，len(回傳) 要等於該事件 outputs 清單長度
'@ | & $py -
```

要點：
- **`app.build()` 是最強的結構驗證**：整棵元件樹＋跨頁元件解包（`build_train_tab` 回 4 / `build_infer_tab` 回 2）＋所有 `.click/.change/.load` 接線都得建得起來才會過。改版面/分頁/跨頁接線後先跑這個。（但 build() **不**會跑 generator → yield tuple 長度 vs outputs 長度的不一致只在執行期才爆，要靠肉眼核對或 headless 跑 trainer。）
- **cp950 主控台**印中文會 `UnicodeEncodeError` → 測試腳本開頭 `sys.stdout.reconfigure(encoding="utf-8")`、或 `$env:PYTHONIOENCODING="utf-8"`。**piped heredoc 的中文字會被 PowerShell ASCII pipeline 轉成 `?`** → 測試含中文字面值時改用 Write 出 UTF-8 暫存 .py 再 `& $py file.py`，跑完刪。
- `gr.Info/gr.Error` 在無 request context 下呼叫可能丟錯，但**動作（存檔/刪除）在它之前已執行**——查檔案系統確認結果即可。
- 接線數量核對：`*PV`/`*R4`/`*FORM` 展開後逐項加總，要跟 handler `return` 的元素數一致（preview_bundle 末位含 assign_dd，共 4）。

## 4. demo 資料是執行階段會被寫回的

`projects/*/`（card.json 隨存基本資料、labels.json 隨分類/畫框）測試後 git 會變髒；
非示範的測試專案（slugify 全中文→`proj`、或 `測試1`→`1` 等）要在結帳前清掉，別 commit 進去。
重生兩個示範：`python samples/make_samples.py`（分類 shapes_demo 單形狀、偵測 shapes_det_demo 多形狀多框）。
改共用畫圖函式注意**別動到分類示範的圖**（random 呼叫順序要保持「先 color 再 margin」才位元一致）。
