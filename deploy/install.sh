#!/bin/bash
# 在 Ubuntu / WSL 安裝：建立 venv、每週更新排程、靜態網站服務。不需要 sudo。
# 用法：deploy/install.sh [port]（預設 8080）
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$PWD"
PORT="${1:-8080}"

if [ ! -x .venv/bin/pip ]; then
    if ! python3 -m venv .venv; then
        rm -rf .venv
        echo "建立 venv 失敗。請先執行：sudo apt install python3-venv" >&2
        exit 1
    fi
fi
.venv/bin/pip install -q -r requirements.txt
chmod +x deploy/update.sh

UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNITS"

cat > "$UNITS/c-parkmap-update.service" <<EOF
[Unit]
Description=c-parkmap 檢查並更新資料
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=$DIR/deploy/update.sh
TimeoutStartSec=45min
EOF

# Persistent：關機或 WSL 未啟動而錯過時間，下次開機後補跑
cat > "$UNITS/c-parkmap-update.timer" <<EOF
[Unit]
Description=c-parkmap 每週一 09:00（台北時間）更新

[Timer]
OnCalendar=Mon *-*-* 09:00:00 Asia/Taipei
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat > "$UNITS/c-parkmap-web.service" <<EOF
[Unit]
Description=c-parkmap 靜態網站

[Service]
ExecStart=/usr/bin/python3 -m http.server $PORT --directory $DIR/site
Restart=on-failure

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now c-parkmap-update.timer c-parkmap-web.service

# 讓服務在未登入時也繼續執行；無權限時只提示，不中斷安裝
if ! loginctl enable-linger "$(id -un)" 2>/dev/null; then
    echo "提醒：請執行 sudo loginctl enable-linger $(id -un)，否則登出後服務會停止。" >&2
fi

echo "網站：http://localhost:$PORT/"
systemctl --user list-timers c-parkmap-update.timer --no-pager
