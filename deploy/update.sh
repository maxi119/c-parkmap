#!/bin/bash
# 每週排程執行：檢查 PDF、更新資料，有差異就 commit 到本機 repo（不 push）。
# 失敗時以非 0 結束，既有的 site/ 不會被改動。
set -euo pipefail
cd "$(dirname "$0")/.."

args=("$@")
# 每月第一週更新路網，讓 OSM 的修正能反映到地圖
if [ "$(TZ=Asia/Taipei date +%-d)" -le 7 ]; then
    args+=(--refresh-osm)
fi

.venv/bin/python update_moto_parking.py "${args[@]}"

git add site data
if ! git diff --cached --quiet; then
    git -c user.name="c-parkmap" -c user.email="c-parkmap@localhost" \
        commit -q -m "更新資料：$(TZ=Asia/Taipei date +%F)"
fi
