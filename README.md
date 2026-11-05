# 臺北市路邊機車收費路段地圖

**網站：<https://maxi119.github.io/c-parkmap/site/>**

非官方網站。從[停管處「停車收費資訊」](https://pma.gov.taipei/Content_List.aspx?n=0EC7D6A1032AA0D6)取得《路邊機車收費路段一覽表》PDF，
用 OpenStreetMap 路網把路段轉成線段，發布成網頁地圖，也提供可匯入 Google My Maps 的 KML。
位置為推算結果，實際收費範圍以現場標線與官方公告為準。

## 架構

```text
Ubuntu / WSL 或 GitHub Actions
  → 停管處 PDF 有變更？ → 解析 + OSM 定位 → 產生 GeoJSON / KML
  → 更新 site/ → 網站提供最新內容
```

- PDF 未變更時只更新 `site/data/status.json` 的最後檢查時間。
- Ubuntu / WSL 每週一 09:00（台北時間）自動更新，每月第一週會重新下載 OSM 路網。
- GitHub Actions 的自動排程已關閉，不會定期更新 <https://maxi119.github.io/c-parkmap/site/>。

## 用 GitHub Actions 手動更新

到 repo 的 **Actions → update → Run workflow**。推送網站、`overrides.csv`、主程式、`requirements.txt` 或 workflow 的變更時也會執行。任何一步失敗都不會部署，GitHub 會寄失敗通知信。

| 選項 | 用途 |
|---|---|
| （都不勾） | 檢查 PDF，有變更才重建 |
| `force` | PDF 未變更也重建 |
| `refresh_osm` | 重新下載 OSM 路網並重建 |

## 本機執行

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python update_moto_parking.py --force
.venv\Scripts\python -m http.server --directory site   # 預覽 http://localhost:8000
```

## 在 Ubuntu / WSL 上執行

不需要 GitHub Actions 也能運作。已在 WSL 的 Ubuntu 26.04（Python 3.14）測試更新流程與網站；`install.sh` 尚未實跑。

```bash
sudo apt install python3-venv git
git clone https://github.com/maxi119/c-parkmap.git ~/c-parkmap
cd ~/c-parkmap
bash deploy/install.sh 8080
```

會安裝兩個 user-level systemd 服務：

- `c-parkmap-update.timer`：每週一 09:00（台北時間）執行 `deploy/update.sh`，錯過時間時會在下次開機後補跑。結果只 commit 到本機，不會 push。
- `c-parkmap-web.service`：用 `python3 -m http.server` 提供 `site/`，網址為 `http://<主機>:8080/`。

常用指令：

```bash
systemctl --user start c-parkmap-update.service   # 立刻更新一次
journalctl --user -u c-parkmap-update.service     # 查看紀錄
```

注意事項：

- **WSL**：WSL 閒置時會停止，服務也會跟著停，只適合自己使用。
- **「我的位置」**：瀏覽器只允許在 HTTPS 或 localhost 下定位。用區網 IP 開啟時這個按鈕不會動作；公開服務請在前面加 nginx 或 Caddy 提供 HTTPS。
- **避免衝突**：Actions 的自動排程已關閉，但手動或推送觸發時仍會 commit `status.json`，之後在主機上 `git pull` 可能發生衝突。
- **路網來源**：不同 Overpass 鏡像的資料可能有差異，更新路網後數量可能略有變動。

## 輸出

| 檔案 | 內容 |
|---|---|
| `site/data/segments.geojson` | 網站用的路段線段與結構化時段 |
| `site/data/status.json` | 資料版本、最後檢查與更新時間、來源、各精度數量 |
| `site/data/missing.json` | 未畫出的路段 |
| `site/downloads/taipei_moto_parking_all.kml` | 全部路段，依收費時段上色 |
| `site/downloads/layers/*.kml` | 依收費時段分檔，一檔一層 |
| `site/downloads/segments.csv` | 每個路段的定位結果 |
| `site/downloads/needs_review.csv` | 位置不精確或未畫出、需人工確認的路段 |
| `data/CHANGES.md` | 每次改版新增、移除的路段 |
| `data/state.json` | 判斷是否改版用的狀態，勿手動修改 |

位置精度：

| 值 | 意義 |
|---|---|
| `exact` | 在兩端路口之間切分（距路口 30 公尺內） |
| `near` | 同上，但路口距離較遠（150 公尺內），或 PDF 段別有誤，端點可能略有偏差 |
| `approx` | 找不到起訖點，畫出整條道路，網站以虛線顯示 |
| `missing` | OSM 查無此路名（多為無名巷、地標），不會出現在地圖上，網站另外列出 |
| `manual` | 依 `overrides.csv` 人工標註 |

## 人工修正

建立或修改 `overrides.csv`（UTF-8），路段與起訖要和 PDF 或 `needs_review.csv` 完全相同。座標可從 Google 地圖右鍵複製，多點以分號分隔：

```csv
路段,路段起訖,座標
景文街282巷,景文街-景後街,"24.99001,121.54001;24.99101,121.54101"
```

範例座標僅示範格式。推上 `main` 後，Actions 會自動重建並部署。

## 匯入 Google My Maps

My Maps 沒有公開的寫入 API，匯入必須手動。從網站的「下載資料」取得 KML：

- **第一次**：到 [mymaps.google.com](https://www.google.com/maps/d/) 建立地圖 → 匯入全部路段的 KML。
- **資料更新後**：點圖層的「⋮」→「重新匯入並合併」→「取代所有項目」，選新下載的檔案。

想依時段開關圖層時，改為每個時段各新增一層，分別匯入各時段的 KML。

## 資料與授權

- 路段資料：臺北市停車管理工程處，依其[著作權聲明](https://pma.gov.taipei/News_Content.aspx?n=2D376B94B447B084&sms=B01AA4D38E103E2A&s=F704FA8B68BCC5D9&ccms_cs=1)註明出處。原始 PDF 不收錄在 repo。
- 道路幾何：© OpenStreetMap contributors（ODbL），公開分享地圖時請保留標示。
- 底圖：內政部國土測繪中心。
- 地圖函式庫：[Leaflet](https://leafletjs.com/) 1.9.4（BSD-2-Clause），收錄於 `site/vendor/`。
