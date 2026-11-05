# 臺北市路邊機車收費路段 → Google My Maps

從[停管處「停車收費資訊」](https://pma.gov.taipei/Content_List.aspx?n=0EC7D6A1032AA0D6)取得《路邊機車收費路段一覽表》PDF，
用 OpenStreetMap 路網把路段轉成線段，輸出可匯入 Google My Maps 的 KML。

## 安裝

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python update_moto_parking.py --force   # 第一次產生輸出
.\register_task.ps1                                    # 註冊每週排程
```

## 匯入 Google My Maps

My Maps 沒有公開的寫入 API，**匯入這一步必須手動**；腳本能自動做到的是偵測更新、重建 KML、跳通知。

- **第一次**：到 [mymaps.google.com](https://www.google.com/maps/d/) 建立地圖 → 匯入 `output/taipei_moto_parking_all.kml`。
- **之後收到通知時**：點圖層的「⋮」→「重新匯入並合併」→「取代所有項目」，選同一個檔案。

想依時段開關圖層時，改為每個時段各新增一層，分別匯入 `output/layers/*.kml`。

## 輸出

| 檔案 | 內容 |
|---|---|
| `output/taipei_moto_parking_all.kml` | 全部路段，依收費時段上色 |
| `output/layers/*.kml` | 依收費時段分檔，一檔一層 |
| `output/segments.csv` | 每個路段的定位結果 |
| `output/needs_review.csv` | 位置不精確或未畫出、需人工確認的路段 |
| `output/CHANGES.md` | 每次更新新增、移除的路段 |
| `update.log` | 執行紀錄 |

位置精度：

| 值 | 意義 |
|---|---|
| `exact` | 在兩端路口之間切分（距路口 30 公尺內） |
| `near` | 同上，但路口距離較遠（150 公尺內），或 PDF 段別有誤，端點可能略有偏差 |
| `approx` | 找不到起訖點，畫出整條道路，名稱標 `[約略]` |
| `missing` | OSM 查無此路名（多為無名巷、地標），**不會出現在地圖上** |
| `manual` | 依 `overrides.csv` 人工標註 |

## 人工修正

建立 `overrides.csv`（UTF-8），路段與起訖要和 PDF 或 `needs_review.csv` 完全相同。座標可從 Google 地圖右鍵複製，多點以分號分隔：

```csv
路段,路段起訖,座標
景文街282巷,景文街-景後街,"24.99001,121.54001;24.99101,121.54101"
```

修改後執行 `update_moto_parking.py --force`。範例座標僅示範格式。

## 資料與授權

- 路段資料：臺北市停車管理工程處。地圖位置為推算結果，實際收費範圍以現場標線與官方公告為準。
- 道路幾何：© OpenStreetMap 貢獻者（ODbL），公開分享地圖時請保留標示。
