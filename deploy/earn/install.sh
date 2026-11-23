#!/usr/bin/env bash
# Install or update the Earn service on kituwa-cloud-1.
# Run as root on the host, after the code has been unpacked in /opt/k3s-earn.
set -euo pipefail

APP=/opt/k3s-earn
DATA=/var/lib/k3s-earn
ENV=/etc/k3s-earn/earn.env

[ -f "$APP/earn/__init__.py" ] || { echo "earn: code is not in $APP" >&2; exit 1; }
[ -f "$ENV" ] || { echo "earn: $ENV is missing (copy earn.env.example and fill it)" >&2; exit 1; }

if ! id -u k3s-earn >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin k3s-earn
fi

install -d -o root -g root -m 0755 "$APP"
install -d -o k3s-earn -g k3s-earn -m 0750 "$DATA"
install -d -o root -g root -m 0755 /etc/k3s-earn
chown root:root "$ENV"
chmod 0600 "$ENV"
chown -R root:root "$APP"

if [ ! -x "$APP/.venv/bin/python" ]; then
  python3 -m venv "$APP/.venv"
fi
"$APP/.venv/bin/pip" -q install --upgrade pip
"$APP/.venv/bin/pip" -q install -r "$APP/requirements-earn.txt"

install -m 0644 "$APP/deploy/earn/k3s-earn.service" /etc/systemd/system/k3s-earn.service
install -m 0644 "$APP/deploy/earn/k3s-earn-ingest.service" /etc/systemd/system/k3s-earn-ingest.service
install -m 0644 "$APP/deploy/earn/k3s-earn-ingest.timer" /etc/systemd/system/k3s-earn-ingest.timer

systemctl daemon-reload
systemctl enable k3s-earn.service >/dev/null
systemctl restart k3s-earn.service
systemctl enable --now k3s-earn-ingest.timer >/dev/null
sleep 1
systemctl --no-pager --quiet is-active k3s-earn.service
echo "earn: service active on 127.0.0.1:8141"
