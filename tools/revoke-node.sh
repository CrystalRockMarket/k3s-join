#!/usr/bin/env bash
# Remove a worker from the cluster and let its pods drain elsewhere.
#
#   ./tools/revoke-node.sh spare-pc
#   ./tools/revoke-node.sh spare-pc --dry-run
#
# This deletes the Node object on the control plane. It does not touch the
# machine itself: the agent there keeps running and will re-register if it can.
# For a machine that is gone for good, also revoke its join code and remove the
# device from the tailnet.

set -euo pipefail

SERVER_HOST="${KITUWA_SERVER_HOST:-kituwa-cloud-1}"
NODE=""
DRY_RUN=0

say() { printf '%s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) say "revoke-node.sh <node-name> [--dry-run]"; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) NODE="$1"; shift ;;
  esac
done

[ -n "$NODE" ] || die "give the node name, for example: ./tools/revoke-node.sh spare-pc"

say "nodes before:"
ssh "$SERVER_HOST" "sudo -n k3s kubectl get nodes --no-headers" 2>/dev/null | sed 's/^/  /' >&2

if ! ssh "$SERVER_HOST" "sudo -n k3s kubectl get node '$NODE' --no-headers" >/dev/null 2>&1; then
  die "no node named $NODE on the control plane"
fi

if [ "$DRY_RUN" = "1" ]; then
  say "would run: kubectl delete node $NODE"
  exit 0
fi

ssh "$SERVER_HOST" "sudo -n k3s kubectl delete node '$NODE'" >&2
say ""
say "removed $NODE. Pods that were on it are rescheduled by the control plane."
say "If the machine is still powered on, its agent will re-register within a minute;"
say "run the uninstall there to stop it for good:"
say "  curl -fsSL https://k3s.kituwa.com/install.sh | sudo sh -s -- --uninstall --remove-tailscale"
