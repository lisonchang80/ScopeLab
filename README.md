# ScopeLab — 影像辨識標註工作台

建立「訓練用」影像標註資料集的桌面工具。沿用 [ClinScope](../ClinScope) 的三視窗版面與海軍藍／檸檬黃視覺，但用途從「辨識」改為「標註」：把一堆未分類影像，整理成可直接餵進 ClinScope / MatScope 訓練流程的資料集。

支援兩種任務（建專案時選）：

- **分類 cls** — 整張圖一個類別 → 匯出 ImageFolder（類別資料夾）
- **偵測 det** — 在圖上畫 bounding box → 匯出 YOLO / COCO

## 三視窗

| 視窗 | 位置 | 功能 |
|------|------|------|
| **基本資料 BASIC INFO** | 左上 | 填寫要做的模型／資料集資訊：名稱、科別、任務（分類／偵測）、類別清單、作者、版本、授權、說明。存成 `projects/<slug>/card.json`。 |
| **影像編輯 IMAGE EDIT** | 右上 | 顯示選取影像；點圖（或「🔍 放大編輯」）進**全螢幕編輯器**：滾輪縮放 / 拖曳平移、裁切、左右旋轉、水平／垂直翻轉、亮度／對比／銳化（進階）、偵測模式可畫框 / 選框 / 刪框 / 指派類別，存回。 |
| **分類 CLASSIFY** | 下方 | 「📁 選擇資料夾」載入未分類影像 → 分頁（總表 / Null / 各類別 / **＋ 新增類別**）逐張分類；詳細表內嵌下拉直接改分類，或鍵盤數字鍵 1–9 快速指派、方向鍵切換。「💾 匯出資料集」輸出。 |

## 快速開始

```bat
run.bat
```
（沿用 ClinScope 的 venv；瀏覽器開 http://127.0.0.1:7861）

內建示範專案：**形狀分類示範**（cls）、**形狀偵測示範**（det），資料在 `samples/shapes/`。
重新產生示範資料：`python samples/make_samples.py`

## 標註流程

1. 左上「基本資料」填名稱、選任務（分類／偵測）、填類別清單 → **💾 儲存基本資料**
2. 下方「📁 選擇資料夾」載入未分類影像
3. 分類：詳細表下拉指派類別，或數字鍵 1–9；偵測：點 ✎ 進編輯器畫框
4. 需要的話點影像進全螢幕編輯器做裁切／旋轉／增強，存回
5. **💾 匯出資料集**

## 匯出格式

| 任務 | 對接平台 | 通用 |
|------|----------|------|
| 分類 | ImageFolder 資料夾（`類別/影像.jpg`）+ `card.json` | ZIP（同結構） |
| 偵測 | YOLO（`images/` + `labels/*.txt` + `data.yaml`）+ `card.json` | COCO（`annotations.json`）／ZIP(YOLO) |

匯出到 `exports/<slug>__<格式>/`。分類的 ImageFolder 可直接給 ClinScope `add-dataset-track` 的 `prepare_data` 切分；偵測的 YOLO `data.yaml` 可直接給 `train_yolo`。

## 結構

```
app.py                 Gradio 三視窗 UI + 事件接線 + 全螢幕編輯器(JS)
src/store.py           專案 / 標註持久層（card.json、labels.json）
src/imaging.py         讀圖、畫框預覽、編輯器 dataURL 存檔
src/exporter.py        ImageFolder / YOLO / COCO 匯出
projects/<slug>/       每個標註專案：card.json + labels.json + edited/
samples/               內建示範影像與專案
exports/               匯出輸出（git 忽略）
```

### 資料格式

`labels.json`：
- 分類：`{"labels": {"<影像路徑>": "類別名"}, "edits": {"<原圖>": "<編輯後圖>"}}`
- 偵測：`{"labels": {"<影像路徑>": [{"cls": "類別", "x": .., "y": .., "w": .., "h": ..}]}, ...}`
  （座標皆正規化 0–1，左上角 x,y + 寬高 w,h）

## 部署

公開上線可比照 ClinScope：環境變數 `SCOPELAB_AUTH_USER` / `SCOPELAB_AUTH_PASS` 開帳密登入、
`SCOPELAB_ALLOWED_PATHS`（分號分隔）收緊可讀資料夾、`SCOPELAB_PORT` 改埠（預設 7861）。

> 研究／作品集用途。
