#!/usr/bin/env bash
# Claim a Kituwa cluster node for an Earn account.
#
# Run on the machine itself, as root:
#   curl -fsSL https://k3s.kituwa.com/claim.sh | sudo bash -s -- --code 'k3sclaim_…'
#
# It reads the node name from the k3s config (or the short hostname), checks that
# k3s is running, and reports the one-time code. It sends nothing else.

set -euo pipefail

CODE=""
NODE=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --code) CODE="${2:-}"; shift 2 ;;
    --node) NODE="${2:-}"; shift 2 ;;
    -h|--help)
      printf 'usage: claim.sh --code k3sclaim_… [--node NAME]\n'
      exit 0 ;;
    *) printf 'claim: unknown argument %s\n' "$1" >&2; exit 2 ;;
  esac
done

die() { printf 'claim: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root (sudo)"
[ -n "$CODE" ] || die "missing --code"
case "$CODE" in k3sclaim_*) ;; *) die "that does not look like a claim code" ;; esac

if command -v systemctl >/dev/null 2>&1; then
  systemctl is-active --quiet k3s-agent || die "k3s-agent is not running on this machine"
fi

if [ -z "$NODE" ]; then
  if [ -r /etc/rancher/k3s/config.yaml ]; then
    NODE="$(awk -F: '/^[[:space:]]*node-name[[:space:]]*:/ {gsub(/[[:space:]"'"'"']/, "", $2); print $2; exit}' /etc/rancher/k3s/config.yaml || true)"
  fi
fi
[ -n "$NODE" ] || NODE="$(hostname -s)"
[ -n "$NODE" ] || die "could not determine the node name; pass --node"

# The only outbound request: the one-time code and the node name.
BODY="$(printf '{"code":"%s","node":"%s"}' "$CODE" "$NODE")"
RESPONSE="$(curl -fsS --max-time 20 -X POST \
  -H 'Content-Type: application/json' \
  --data "$BODY" \
  https://k3s.kituwa.com/api/v1/earn/claim)" || die "the claim request failed"

printf '%s\n' "$RESPONSE"
case "$RESPONSE" in
  *'"ok":true'*) printf '\nLinked. This node now earns for your account.\n' ;;
  *) die "the cluster refused the claim" ;;
esac
