# GitHub Pages 版本實作計畫

狀態：**程式與自動更新已完成；執行環境改為以 WSL / Ubuntu 為主，GitHub Actions 非必要**（規劃日期 2026-11-05，最後更新 2026-11-05）

## 方向調整（2026-11-05）

擁有者決定不必堅持 GitHub Actions，本機 WSL 能正常運作即可，之後再找 Ubuntu 主機放置。

目前網址：<https://maxi119.github.io/c-parkmap/site/>。根網址顯示 README 是因為 Pages 來源為「Deploy from branch」；不走 Actions 部署的話，不必切換。

WSL（Ubuntu 26.04、Python 3.14.4）實測：

- [x] 依 `requirements.txt` 安裝成功。
- [x] `--force` 重建：exact 461、near 24、approx 39、missing 22，與基準相同，`git diff` 只有 `checked_at`。
- [x] `deploy/update.sh` 實跑成功，96 秒內更新路網並在本機 commit。
- [x] 暫時的 `http.server` 提供 `site/`，WSL 內與 Windows 的 `localhost` 都回 200。
- [ ] `deploy/install.sh` **尚未實跑**：常駐服務的安裝被權限檢查擋下，需由擁有者執行。目前只確認過腳本語法與 timer 的時間設定。
- [ ] 系統的 `python3-venv` 未安裝（需要 sudo）。測試時用 `--without-pip` 加 `get-pip.py` 代替。

更新路網時的發現：`overpass-api.de` 回 502，改由 `overpass.kumi.systems` 提供，道路數 18,061（先前 18,142），`堤頂大道2段407巷20弄` 因此變成 missing（exact 460、missing 23）。原因是資料來源不同，不是程式問題；尚未確認是鏡像落後還是 OSM 實際變更。測試結果沒有進入 repo。

## 待辦（需要 repo 擁有者操作）

1. **在 WSL 安裝服務**：`sudo apt install python3-venv` 後執行 `bash deploy/install.sh 8080`（測試副本在 `~/c-parkmap`）。
2. **移除 Windows 排程**（階段 4）：Actions 的自動排程已關閉（2026-11-05），WSL 服務裝好後就只留一個地方更新。在那之前，`/site/` 的資料不會定期更新，只有手動或推送觸發時才會。
3. **決定資料與底圖的使用是否可接受**（見下方「開始前要決定」的查核結果）。
4. **若仍要用 Actions 部署到根網址**：Settings → Pages → Source 改為 **GitHub Actions**，切換後 `/site/` 會失效；並確認帳號有開失敗通知。

## 目標

把「手動匯入 My Maps」換成全自動：PDF 改版後，公開網頁地圖自動更新，不需要人工操作。

```text
GitHub Actions（每週）
  → 停管處 PDF 有變更？ → 解析 + OSM 定位 → 產生 GeoJSON / KML
  → commit 回 repo → 部署 GitHub Pages → 手機、電腦直接開網址
```

不在範圍內：自架 OSM server、圖磚伺服器、後端、帳號系統、其他縣市。

## 現況

- 主程式：`update_moto_parking.py`。沿用 `parse_pdf`、`locate`、`load_osm`、`load_overrides`，不重寫定位邏輯。
- 基準結果（115 年 9 月版，共 546 段）：exact 461、near 24、approx 39、missing 22。
- 本機排程 `TaipeiMotoParkingUpdate` 每週一 09:00 執行，並用 Windows 通知提醒。移除前，本機執行也會改動 `site/`、`data/`，不要把這些變更 commit。
- Repo：<https://github.com/maxi119/c-parkmap>（公開）。`cache/`、`archive/` 已列入 `.gitignore`。
- 自動 commit 會更新 `main`，本機要改東西前先 `git pull`。

## 開始前要決定

- [x] **Repo 名稱與帳號**：`maxi119/c-parkmap`，網址 `https://maxi119.github.io/c-parkmap/`。
- [x] **是否接受公開**：repo 已公開，Pages 已由擁有者開啟。
- [ ] **再利用條款**：已查核，但**未取得停管處明確授權，需擁有者決定**。
  - [停管處著作權聲明](https://pma.gov.taipei/News_Content.aspx?n=2D376B94B447B084&sms=B01AA4D38E103E2A&s=F704FA8B68BCC5D9&ccms_cs=1)：以該處名義公開發表的著作，在合理範圍內得重製、公開傳輸，並請註明出處；連結須標示該處名稱且不得誤導。
  - 已照做：網站與 README 註明出處並連回來源頁、標示非官方、不收錄原始 PDF、不營利。
  - 「合理範圍」沒有明確界線。要完全確定，可去函停管處詢問。

## 階段 0：先驗證風險 ✅

**結論：全程在 Actions 執行。**（2026-11-05 實測）

- [x] 建立 repo，用測試 workflow 執行 `find_pdf_link()` 和 `download_pdf()`：成功，約 5 秒。
- [x] 測試 Overpass 下載：成功，但需要重試。
  - 第一次與第二次測試失敗：`overpass-api.de` 回 502（滿載），`overpass.kumi.systems`、`overpass.private.coffee` 逾時，`maps.mail.ru` 回 504。
  - 同一時段從本機測試：`overpass-api.de` 52 秒成功，其餘三個結果相同，所以判斷是公共服務忙碌，不是 Actions 的 IP 被擋。
  - 加上重試後，在 Actions 用 366 秒成功下載 18,142 條道路。
- [x] 依結果選擇執行方式：**Actions 連得上**。測試 workflow 已刪除。
- 因應措施：`load_osm` 最多重試 4 輪，每輪間隔 2 分鐘；路網存在 `actions/cache`，下載失敗時改用舊路網。

## 階段 1：調整資料管線 ✅

- [x] 新增 `geojson_document()`，輸出 `site/data/segments.geojson`。（函式名稱與原計畫的 `write_geojson()` 不同）
  - 每個路段一個 Feature，`MultiLineString`，用來保留雙向車道的兩條線。
  - properties：`region`、`schedule`、`road`、`span`、`quality`、`note`。
  - 另外輸出結構化時段：`days`（ISO 星期）、`start`、`end`、`holidays`。
- [x] 新增 `site/data/status.json`：資料版本、最後檢查時間、最後更新時間、PDF 來源網址、各精度數量，以及圖例用的時段與顏色。
- [x] 新增 `site/data/missing.json`：未畫出的路段。
- [x] KML 移到 `site/downloads/`；`segments.csv`、`needs_review.csv` 也一起移過去，`output/` 已移除。
- [x] `state.json` 移到 `data/state.json`。
- [x] `CHANGES.md` 移到 `data/CHANGES.md`，不會被重建流程清除。
- [x] `--notify` 維持為選用；Actions 不帶這個參數。
- [x] 新增 `--refresh-osm`：強制重新下載路網並重建。
- [x] 確保輸出穩定：固定排序與換行（含 `.gitattributes`），輸出不含執行時間。
- [x] 額外：`overrides.csv` 或主程式變更時自動重建，不必記得加 `--force`。

**驗收** ✅：`--force` 結果與基準數字相同；連跑兩次、以及 PDF 未變更時，都只有 `status.json` 的 `checked_at` 不同。

## 階段 2：前端（`site/`，不用建置工具）✅

純 HTML / CSS / JS + Leaflet 1.9.4（放在 `site/vendor/`）。

必要功能：

- [x] 依收費時段上色，圖例可開關各時段。
- [x] 點線段顯示：路段、起訖、區域、收費時間、位置精度、備註。
- [x] `approx` 用虛線與較淡的顏色，並標示「約略位置」。
- [x] 顯示資料版本與最後檢查時間；超過 21 天未檢查時顯示「資料可能過期」。
- [x] 手機版可正常使用。
- [x] 頁面列出未畫出的路段，以及免責聲明與來源。

加值功能：

- [x] 「現在收費中」篩選：用 `Asia/Taipei` 時間判斷，並明確標示國定假日未納入判斷。
- [x] 路名、區域搜尋。
- [x] 「我的位置」按鈕。實機定位尚未測試，需在手機上開啟確認。

注意事項：

- [x] 彈出視窗用 `textContent` 組內容。
- [ ] 底圖使用條款：**未找到官方授權原文**。
  - 已改用內政部國土測繪中心 WMTS（`EMAP`），不使用 `tile.openstreetmap.org`，並標示來源。
  - 這是免申請的公開服務，但 `https://whgis-nlsc.moi.gov.tw/Opendata/Files.aspx` 的授權說明尚未逐條確認。
  - 要換底圖只需改 `site/app.js` 開頭的 `BASEMAP`。
- [x] 所有路徑用相對路徑。

**驗收** ✅：線上網址以桌機（1280×800）與手機（390×844）寬度的無頭 Chrome 開啟，圖例、彈出視窗、虛線樣式、免責聲明都正常，console 沒有錯誤。尚未在實體手機上測試。

## 階段 3：GitHub Actions（`.github/workflows/update.yml`）

> 2026-11-05 已移除 `schedule` 觸發與每月更新路網的判斷，以下為當時的紀錄。保留手動與推送觸發。

- [x] 觸發：`schedule` + `workflow_dispatch`（可選 `force`、`refresh_osm`），另加推送網站、`overrides.csv`、主程式時觸發。
  - 每週一 09:00 台北時間 = cron `0 1 * * 1`。
  - 每月第一個週一帶 `--refresh-osm`。
- [x] 步驟：checkout → 安裝 Python 與 `requirements.txt` → 執行腳本 → 有差異才 commit → 部署 Pages。
- [x] 權限只開必要的，依 job 分開：`contents: write`；`pages: write`、`id-token: write`。
- [x] 不 commit `cache/osm_taipei_roads.json`（過濾後約 13 MB），改用 `actions/cache`。
- [x] 版本固定：`requirements.txt` 鎖定版本。
- [x] **防止排程被停用**：每次檢查都更新並 commit `status.json`。
- [ ] **失敗通知**：依賴 GitHub 的失敗通知信，需要擁有者確認帳號設定。
- [x] 解析失敗時 workflow 失敗且不部署（`deploy` 依賴 `update`，`MIN_EXPECTED_ROWS` 檢查沿用）。

**驗收**：

- [ ] 手動觸發一次，網站成功更新。推送觸發的 run 已成功且 `deploy` job 成功，但 Pages 來源切換前，根網址不會顯示新網站。
- [x] PDF 未變更時，只有 `status.json` 變動。（Actions 上實測，commit `8d830ba`）
- [x] 竄改 `state.json` 的雜湊來模擬改版，確認會重建並寫入異動紀錄。（在本機測試，避免假紀錄進入正式資料）
- [ ] 用壞掉的 PDF 模擬失敗，確認 workflow 失敗、線上網站維持原樣。
  - 本機已確認：空白 PDF 與非 PDF 內容都會報錯，輸出完全不變。
  - 尚未在 Actions 上實測；不部署的部分目前只是依 workflow 設計推論。

## 階段 4：切換與收尾

- [ ] 第一次正式排程成功後（最早 2026-11-09），才移除本機排程，避免兩邊同時更新：
  `Unregister-ScheduledTask -TaskName 'TaipeiMotoParkingUpdate' -Confirm:$false`
  移除後可一併刪除 `register_task.ps1`。
- [x] 更新 `README.md`：網址、架構、手動觸發方式、`overrides.csv` 修改流程。
- [ ] 視需要處理 22 段未畫出與 39 段約略的路段（`overrides.csv` 或回補 OSM），但不阻擋上線。

## 完成條件

- [ ] 公開網址可在手機上使用，能看到完整的收費路段、資料版本與免責聲明。（`/site/` 暫時網址已通過無頭 Chrome 測試，待切換來源與實機確認）
- [ ] 連續兩次排程都正常執行，期間不需要人工操作。
- [ ] 失敗時會收到通知，線上網站維持上一個正常版本。
