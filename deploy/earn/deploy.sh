#!/usr/bin/env bash
# Publish the Earn service code from trinity to kituwa-cloud-1.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST="${KITUWA_HOST:-kituwa-cloud-1}"
cd "$ROOT"

[ -f earn/__init__.py ] || { echo "run from the k3s-join project" >&2; exit 1; }
[ -x .venv/bin/python ] || { echo "missing .venv; create it with requirements-earn.txt" >&2; exit 1; }

echo "==> tests"
.venv/bin/python tests/test_earn.py >/dev/null
bash tests/run-tests.sh >/dev/null

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/app"
cp -a earn requirements-earn.txt "$STAGE/app/"
mkdir -p "$STAGE/app/deploy"
cp -a deploy/earn "$STAGE/app/deploy/"

echo "==> upload"
tar -C "$STAGE/app" -czf - . | ssh -o BatchMode=yes "$HOST" \
  "sudo -n mkdir -p /opt/k3s-earn && sudo -n tar -C /opt/k3s-earn -xzf -"

echo "==> install"
ssh -o BatchMode=yes "$HOST" "sudo -n bash /opt/k3s-earn/deploy/earn/install.sh"

echo "==> health"
ssh -o BatchMode=yes "$HOST" "curl -fsS --max-time 5 http://127.0.0.1:8141/healthz"
echo
