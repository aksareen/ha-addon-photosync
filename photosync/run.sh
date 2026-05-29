#!/usr/bin/env bash
set -e

# s6-overlay stores env vars as files — export SUPERVISOR_TOKEN for Python
if [ -f /run/s6/container_environment/SUPERVISOR_TOKEN ]; then
    SUPERVISOR_TOKEN=$(cat /run/s6/container_environment/SUPERVISOR_TOKEN)
    export SUPERVISOR_TOKEN
fi

CONFIG_PATH="/data/options.json"

koofr_email=$(jq -r '.koofr_email' "$CONFIG_PATH")
koofr_password=$(jq -r '.koofr_password' "$CONFIG_PATH")
remote_path=$(jq -r '.remote_path' "$CONFIG_PATH")
folder_name=$(jq -r '.folder_name' "$CONFIG_PATH")

obscured_password=$(rclone obscure "$koofr_password")

cat > /data/rclone.conf <<EOF
[koofr]
type = webdav
url = https://app.koofr.net/dav/Koofr
vendor = other
user = ${koofr_email}
pass = ${obscured_password}
EOF

export RCLONE_CONFIG="/data/rclone.conf"

echo "[photosync] rclone config generated"
echo "[photosync] remote_path=${remote_path} folder_name=${folder_name}"
echo "[photosync] starting web server on port 8099"

exec python3 -u /app/server.py
