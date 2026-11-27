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
# Search Console's read-only credential (a service account key or the owner's read-only consent), when one is stored (growth.py). Readable by the app's user only.
if aws ssm get-parameter --region $REGION --name /tally/gsc-key --with-decryption --query Parameter.Value --output text > "$DATA/gsc-key.json" 2>/dev/null; then
  chown tally "$DATA/gsc-key.json"; chmod 600 "$DATA/gsc-key.json"; echo "TALLY_GSC_KEY=$DATA/gsc-key.json" >> "$APP/.env"
else rm -f "$DATA/gsc-key.json"; fi
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
# A crawl burst once grew the app until the whole machine was swapping and nothing answered, the watchdog included.
# Past this size the app is stopped and started again (seconds) instead.
MemoryMax=1100M
MemorySwapMax=256M
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
# The instance has under 2 GB of memory: swap keeps a burst of crawling from killing the app.
if ! swapon --show | grep -q /swapfile; then
  [ -f /swapfile ] || { dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none; chmod 600 /swapfile; mkswap -q /swapfile; }
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
echo 'vm.swappiness = 10' > /etc/sysctl.d/90-tally-swap.conf
sysctl -q -p /etc/sysctl.d/90-tally-swap.conf
# Restart the app when it stops answering (it has hung under load without exiting, which systemd alone does not see).
cat > /usr/local/bin/tally-watchdog.sh <<'WATCH'
#!/bin/bash
# Restart Tally when it has stopped answering. Two failed checks 15 seconds apart, and never within two minutes of a start
# (a deploy or a restart is still warming up then).
ok() { curl -fsS -m 10 -o /dev/null http://127.0.0.1:8000/robots.txt; }
since=$(systemctl show tally -p ActiveEnterTimestampMonotonic --value); now=$(awk '{print int($1*1000000)}' /proc/uptime)
[ -n "$since" ] && [ $(( (now - since) / 1000000 )) -lt 120 ] && exit 0
ok && exit 0
sleep 15
ok && exit 0
echo "tally-watchdog: no answer twice, restarting tally" | systemd-cat -t tally-watchdog -p warning
systemctl restart tally
WATCH
chmod 755 /usr/local/bin/tally-watchdog.sh
cat > /etc/systemd/system/tally-watchdog.service <<UNIT
[Unit]
Description=Restart Tally when it stops answering
[Service]
Type=oneshot
ExecStart=/usr/local/bin/tally-watchdog.sh
UNIT
cat > /etc/systemd/system/tally-watchdog.timer <<UNIT
[Unit]
Description=Check Tally every minute
[Timer]
OnBootSec=3min
OnUnitActiveSec=1min
AccuracySec=5s
[Install]
WantedBy=timers.target
UNIT
chown -R tally:tally /opt/tally
systemctl daemon-reload
systemctl enable -q --now tally-watchdog.timer
systemctl enable -q tally caddy
systemctl restart tally
systemctl reload caddy 2>/dev/null || systemctl restart caddy
sleep 2
curl -s -o /dev/null -w "local app: %{http_code}\n" http://127.0.0.1:8000/api/me
git -C "$APP" log --oneline | head -1
