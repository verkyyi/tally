#!/bin/bash
set -ex
dnf install -y -q git python3.11 python3.11-pip
curl -fsSL "https://caddyserver.com/api/download?os=linux&arch=arm64" -o /usr/local/bin/caddy && chmod +x /usr/local/bin/caddy
mkdir -p /etc/caddy /var/lib/caddy /opt/tally
id tally || useradd -r -d /opt/tally tally
git clone -q https://github.com/verkyyi/tally.git /opt/tally/app
bash /opt/tally/app/infra/remote_deploy.sh
