# GitHub Pages 版本實作計畫

狀態：**尚未開始**（規劃日期 2026-11-05）

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
- 本機排程 `TaipeiMotoParkingUpdate` 每週一 09:00 執行，並用 Windows 通知提醒。
- 專案目前不是 git repo。`cache/`、`archive/` 已列入 `.gitignore`。

## 開始前要決定

- [ ] **Repo 名稱與帳號**：網址會是 `https://<帳號>.github.io/<repo>/`。
- [ ] **是否接受公開**：免費方案的 Pages 通常要搭配公開 repo。
- [ ] **再利用條款**：這份 PDF 不是在開放資料平臺取得，公開前先確認停管處網站的資料使用規定。

## 階段 0：先驗證風險（最先做，約 30 分鐘）

**最大風險：GitHub Actions 的海外 IP 可能連不上政府網站。** 這點尚未驗證，結果會決定架構。

- [ ] 建立 repo，加一個只有 `workflow_dispatch` 的測試 workflow，執行 `find_pdf_link()` 和 `download_pdf()`。
- [ ] 同一個 workflow 測試 Overpass 下載。
- [ ] 依結果選擇執行方式：
  - **Actions 連得上**：照階段 3 全程在 Actions 執行。
  - **連不上**：保留本機排程，由本機執行腳本後 `git push`，Actions 只負責部署 Pages。缺點是電腦要開機。不要嘗試繞過對方的連線限制。

## 階段 1：調整資料管線

- [ ] 新增 `write_geojson()`，輸出 `site/data/segments.geojson`。
  - 每個路段一個 Feature，`MultiLineString`，用來保留雙向車道的兩條線。
  - properties：`region`、`schedule`、`road`、`span`、`quality`、`note`。
  - 另外輸出結構化時段（星期、起訖小時），前端不用自己解析中文字串。
- [ ] 新增 `site/data/status.json`：資料版本、最後檢查時間、最後更新時間、PDF 來源網址、各精度數量。
- [ ] 新增 `site/data/missing.json`：未畫出的路段，讓網頁能列出來，不要無聲消失。
- [ ] KML 移到 `site/downloads/`，繼續提供給想用 My Maps 的人。
- [ ] `state.json` 從 `cache/` 移到 repo 內會 commit 的位置（如 `data/state.json`）；Actions 每次都是全新環境，否則每次都會判定為「有變更」。
- [ ] `CHANGES.md` 移到會保留的位置，`write_outputs` 目前會整個重建 `output/`，要確認紀錄不會遺失。
- [ ] `--notify` 維持為選用；Actions 環境不呼叫 Windows 通知。
- [ ] 新增 `--refresh-osm`：強制重新下載路網並重建。
  - 原因：目前只有 PDF 變更時才會重建，所以 OSM 補上的路名不會自動反映。
- [ ] 確保輸出穩定（固定排序、不寫入執行時間），避免內容沒變卻產生 commit。

**驗收**：`--force` 跑出的結果與基準數字相同；連跑兩次，除了 `status.json` 的時間外沒有差異。

## 階段 2：前端（`site/`，不用建置工具）

純 HTML / CSS / JS + Leaflet（固定版本，放在 repo 內或 CDN）。

必要功能：

- [ ] 依收費時段上色，圖例可開關各時段。
- [ ] 點線段顯示：路段、起訖、區域、收費時間、位置精度、備註。
- [ ] `approx` 用虛線與較淡的顏色，並標示「約略位置」。
- [ ] 顯示資料版本與最後檢查時間；超過 21 天未檢查時顯示「資料可能過期」。
- [ ] 手機版可正常使用（這是主要使用情境）。
- [ ] 頁面列出未畫出的路段，以及免責聲明與來源：
  - 「非官方網站，位置為推算結果，實際收費範圍以現場標線與官方公告為準。」
  - 停管處資料來源連結。
  - `© OpenStreetMap contributors`（ODbL）。

加值功能（必要功能完成後再做）：

- [ ] 「現在收費中」篩選：一律用 `Asia/Taipei` 時間判斷，不用裝置時區。
  - **國定假日要明確標示「未納入判斷」**，PDF 的「週六日及例假日」與假日規則不能靠星期推算。
- [ ] 路名、區域搜尋。
- [ ] 「我的位置」按鈕（Pages 為 HTTPS，可用定位 API）。

注意事項：

- [ ] 彈出視窗用 `textContent` 組內容，不要把 PDF 字串直接塞進 `innerHTML`。
- [ ] 底圖先確認圖磚服務的使用條款；公開網站不要預設直接用 `tile.openstreetmap.org`。
- [ ] 所有路徑用相對路徑，因為網站部署在 `/<repo>/` 子路徑下。

**驗收**：用桌機與手機寬度實際開啟，確認圖例、彈出視窗、虛線樣式、免責聲明都正常，console 沒有錯誤。

## 階段 3：GitHub Actions（`.github/workflows/update.yml`）

- [ ] 觸發：`schedule` + `workflow_dispatch`（可選 `force` 參數）。
  - 每週一 09:00 台北時間 = cron `0 1 * * 1`（cron 用 UTC，實際可能延遲）。
  - 每月一次帶 `--refresh-osm`，讓 OSM 的修正能反映到地圖。
- [ ] 步驟：checkout → 安裝 Python 與 `requirements.txt` → 執行腳本 → 有差異才 commit → 部署 Pages。
- [ ] 權限只開必要的：`contents: write`、`pages: write`、`id-token: write`。
- [ ] 不 commit `cache/osm_taipei_roads.json`（約 20 MB），改用 `actions/cache` 或重新下載。
- [ ] 版本固定：`requirements.txt` 鎖定版本，避免 pdfplumber 更新造成解析結果改變。
- [ ] **防止排程被停用**：公開 repo 長期沒有活動時，GitHub 會停用排程。PDF 不常改版，所以每次檢查都要更新並 commit `status.json`（順便提供最後檢查時間）。
- [ ] **失敗通知**：依賴 GitHub 的 workflow 失敗通知信，開通後確認帳號設定有開。
- [ ] 解析失敗時 workflow 要失敗且不部署，保留上一版網站（現有的 `MIN_EXPECTED_ROWS` 檢查要沿用）。

**驗收**：

- [ ] 手動觸發一次，網站成功更新。
- [ ] PDF 未變更時，只有 `status.json` 變動。
- [ ] 竄改 `state.json` 的雜湊來模擬改版，確認會重建並寫入異動紀錄。
- [ ] 用壞掉的 PDF 模擬失敗，確認 workflow 失敗、線上網站維持原樣。

## 階段 4：切換與收尾

- [ ] 第一次正式排程成功後，才移除本機排程，避免兩邊同時更新：
  `Unregister-ScheduledTask -TaskName 'TaipeiMotoParkingUpdate' -Confirm:$false`
  （若階段 0 選擇本機執行，則保留並改成會 `git push`）
- [ ] 更新 `README.md`：網址、架構、手動觸發方式、`overrides.csv` 修改流程。
- [ ] 視需要處理 22 段未畫出與 39 段約略的路段（`overrides.csv` 或回補 OSM），但不阻擋上線。

## 完成條件

- 公開網址可在手機上使用，能看到完整的收費路段、資料版本與免責聲明。
- 連續兩次排程都正常執行，期間不需要人工操作。
- 失敗時會收到通知，線上網站維持上一個正常版本。
