---
name: add-task-engine
description: 在 ScopeLab 加 / 擴一個訓練+推論的任務型態（cls=ResNet、det=YOLO；未來 seg 等同理）。涉及 src/train/{recipes,device,trainer_*,tab}.py、src/infer/{predictor,detector,tab}.py、app.py 跨頁接線。觸發詞：加訓練任務、加 YOLO/分割/關鍵點、訓練分頁、推論引擎、generator 進度、跨分頁帶模型。
---

# ScopeLab 任務引擎（train + infer）接線模板

label → train → infer 全離線、純程式、無 agent。要加一個新任務型態（或改現有的），照下面這套接線走。已落地：**cls（torchvision ResNet）**、**det（ultralytics YOLO）**。

## 檔案分工

```
src/train/recipes.py    # 每任務一組配方 dict（fast/balanced/accurate）
src/train/device.py     # pick_device / batch_for / gpu_stats（共用，勿重寫）
src/train/trainer_<t>.py # 該任務訓練「generator」，逐 epoch yield 事件
src/train/tab.py        # 依 card.task 分派 trainer，generator 串 UI
src/infer/predictor.py  # cls 推論引擎    | src/infer/detector.py  # det 推論引擎
src/infer/tab.py        # 依 model_card.task 分派推論引擎
app.py                  # gr.Tabs id + 跨頁接線
```

## 1. 配方 recipes.py

每任務一個 `<T>_RECIPES = {"fast":{...},"balanced":{...},"accurate":{...}}`；`recipe_choices(task)` / `get_recipe(task,key)` 依任務回對應組。**離線權重**：盡量用本機已快取的（resnet50、yolov8s 有快取；resnet18/yolov8n 沒），trainer 取不到要 try/except 回退（cls→`weights=None` 從頭訓、det→`yolov8s.pt`）。

## 2. trainer_<t>.py 一律寫成 generator

`def train_<t>(name, files, labels, classes, recipe_key, device_info, out_dir, should_stop=None, epochs_override=None)`：
逐步 `yield` 事件 dict，phase ∈ {setup, data, model, epoch, done, error, stopped}。`tab.py` 的同一段 UI 迴圈靠 **phase + key 差異**處理 cls/det（cls epoch 帶 `val_acc/val_f1`，det 帶 `map50/best_map50`）。

- **out_dir = `projects/<slug>/runs/<時間戳>/`**（`projects/*/runs/` 已 gitignore）。收尾寫 `best.pt` + `metrics.json` + `model_card.json`（**一定要有 `"task"` 欄**，推論端靠它分派）。
- **best-on-improve 才存檔**（cls 手動存；YOLO 自動存 → 複製 `runs/<ts>/yolo/weights/best.pt` 到 `runs/<ts>/best.pt`）。
- **torch / ultralytics 在函式內惰性 import**（別放模組頂，免拖慢 app 啟動）。
- **NaN-loss 中止**、`gpu_stats()` 健檢塞進 epoch 事件。

### 阻塞型引擎（YOLO）→ 背景 thread + queue
`YOLO.train` 會卡死整段，generator 沒法直接 yield。做法：`threading.Thread` 跑 train，掛 `on_fit_epoch_end` callback 把 mAP `queue.put`，主執行緒 `while: q.get()` 再 yield。**雷**：① thread 內跑 YOLO 沒 signal 問題、`workers=0`、`plots=False`、`batch=-1` 自動 ② 訓練後 ultralytics 會對 best.pt 再驗一次 → 多丟一個 `epoch>total` 事件，要 `if ev["epoch"]>epochs: continue` 過濾 ③ runs 寫去哪用 `project=str(out_dir), name="yolo"` 覆寫（預設會落 ClinScope/runs）。

## 3. 推論引擎 + 分派

cls→`predictor.load_model`(重建 ResNet、`weights_only=False`)+`predict`(softmax top-k)；det→`detector.predict_det`(YOLO、`r.plot()[:,:,::-1]` BGR→RGB 畫框)。`infer/tab.py` 讀 `model_card.task` 分派：cls 機率表、det 畫框 gallery。

## 4. 跨分頁接線（app.py）

- `with gr.Tabs() as tabs:`，每個 `gr.Tab(..., id="label"/"train"/"infer")`。
- `build_train_tab()` 回 `(proj_dd, recipe_radio, infer_jump_btn, last_run)`；`build_infer_tab()` 回 `(proj_dd, run_dd)`（建頁函式在 Blocks context 內，回傳元件給 app 接線）。
- 跨頁跳轉：handler 回 `gr.update(selected="train")` + 目標頁元件的 `gr.update(choices=..., value=...)`。設另一頁 dropdown 值**不會**觸發它的 .change → 要在同一 handler 一起把連動項（如配方/識別卡）也設好。
- 訓練 generator 要帶出結果給「用此模型推論」：on_train 多兩個輸出（`infer_jump_btn` 解鎖、`last_run` State 寫 `{slug,weights}`），**每個 yield 的 tuple 元素數要等於 outputs 清單長度**（cls/det 共用，現為 7）。
- 兩頁的專案下拉都有 build-time 凍結 → 各放一顆 🔄 刷新鈕（`_all_projects()` 重讀磁碟）。

## 5. 驗證（headless 為主）

`app.build()` 是最強結構檢查（整棵元件樹＋跨頁解包＋接線都建得起來才過）。trainer/predictor 直接 headless 跑小資料（shapes_demo / shapes_det_demo，`epochs_override` 壓低）確認產出 best.pt/metrics/model_card。UI 互動再用 preview（見 [[dev-verify]]）。
