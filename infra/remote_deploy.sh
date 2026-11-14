#!/bin/bash
# Runs on the EC2 instance (as root, through SSM or first boot): pull code, refresh config, restart.
set -euo pipefail
export HOME=/root
REGION=us-east-1
APP=/opt/tally/app
DATA=/opt/tally/data
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
mkdir -p "$DATA"
git config --global --add safe.directory "$APP"
if [ -d "$APP/.git" ]; then git -C "$APP" fetch -q origin main && git -C "$APP" reset -q --hard origin/main
else git clone -q https://github.com/verkyyi/tally.git "$APP"; fi
[ -d "$APP/.venv" ] || python3.11 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
# Secrets live in SSM Parameter Store (encrypted), never in the repo or the image.
umask 077
aws ssm get-parameter --region $REGION --name /tally/env --with-decryption --query Parameter.Value --output text > "$APP/.env"
echo "TALLY_DB=$DATA/tally.db" >> "$APP/.env"
if [ ! -f "$DATA/tally.db" ] || [ "${1:-}" = "--restore-db" ]; then
  systemctl stop tally 2>/dev/null || true
  rm -f "$DATA"/tally.db-wal "$DATA"/tally.db-shm
  aws s3 cp --quiet "s3://tally-data-$ACCOUNT/tally.db" "$DATA/tally.db"
fi
DOMAINS=$(grep '^TALLY_DOMAINS=' "$APP/.env" | cut -d= -f2-)
cat > /etc/caddy/Caddyfile <<CADDY
${DOMAINS:-:80} {
	encode gzip
	reverse_proxy 127.0.0.1:8000
}
CADDY
cat > /etc/systemd/system/tally.service <<UNIT
[Unit]
Description=Tally
After=network-online.target
[Service]
User=tally
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/caddy.service <<UNIT
[Unit]
Description=Caddy
After=network-online.target
[Service]
ExecStart=/usr/local/bin/caddy run --config /etc/caddy/Caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile
Environment=XDG_DATA_HOME=/var/lib/caddy XDG_CONFIG_HOME=/var/lib/caddy
Restart=always
AmbientCapabilities=CAP_NET_BIND_SERVICE
[Install]
WantedBy=multi-user.target
UNIT
chown -R tally:tally /opt/tally
systemctl daemon-reload
systemctl enable -q tally caddy
systemctl restart tally
systemctl reload caddy 2>/dev/null || systemctl restart caddy
sleep 2
curl -s -o /dev/null -w "local app: %{http_code}\n" http://127.0.0.1:8000/api/me
git -C "$APP" log --oneline | head -1
