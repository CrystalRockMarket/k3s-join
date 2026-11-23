#!/usr/bin/env bash
# Mint a single-use invitation (a "join code") for the Kituwa k3s cluster.
#
# Owner-side tool. It never leaves this machine except as one line of text you
# hand to the person joining. It needs the control plane over SSH.
#
#   ./tools/mint-join-code.sh --name mikes-laptop
#   ./tools/mint-join-code.sh --name spare-pc --ttl 24h
#   ./tools/mint-join-code.sh --revoke mikes-laptop
#
# The code contains three things:
#   server          https://<tailnet-ip>:6443
#   token           a k3s bootstrap token limited to agent usage
#   tailscale_key   a Tailscale auth key that carries tag:k3s
#
# The Tailscale key is the only part this script cannot create by itself: the
# tailnet has no API token on this host, so the key comes from
# ~/lock/tailscale_authkey.txt (0600) or --tailnet-key-file. Once that file
# exists, minting is one command with no browser involved.

set -euo pipefail

SERVER_HOST="${KITUWA_SERVER_HOST:-kituwa-cloud-1}"
SERVER_URL="${KITUWA_SERVER_URL:-https://100.120.24.106:6443}"
TAILNET_KEY_FILE="${KITUWA_TAILNET_KEY_FILE:-$HOME/lock/tailscale_authkey.txt}"
K3S_TOKEN_TTL="0"
NODE_NAME=""
REVOKE=""
DRY_RUN=0
CODE_PREFIX="k3s1."

say() { printf '%s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
mint-join-code.sh — issue or revoke one cluster invitation.

  --name NAME            node name to suggest (required unless --revoke)
  --ttl DURATION         bootstrap token lifetime (default 0 = no expiry,
                         because a k3s agent re-reads its token on every start;
                         revoke with --revoke instead of relying on the clock)
  --tailnet-key-file P   Tailscale auth key file (default ~/lock/tailscale_authkey.txt)
  --server-url URL       control plane URL (default https://100.120.24.106:6443)
  --revoke NAME          delete the bootstrap tokens minted for NAME
  --dry-run              print what would happen, mint nothing
  -h, --help             this text
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --name)             NODE_NAME="${2:?}"; shift 2 ;;
    --ttl)              K3S_TOKEN_TTL="${2:?}"; shift 2 ;;
    --tailnet-key-file) TAILNET_KEY_FILE="${2:?}"; shift 2 ;;
    --server-url)       SERVER_URL="${2:?}"; shift 2 ;;
    --revoke)           REVOKE="${2:?}"; shift 2 ;;
    --dry-run)          DRY_RUN=1; shift ;;
    -h|--help)          usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

sanitize() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' \
    | sed -e 's/[^a-z0-9-]/-/g' -e 's/--*/-/g' -e 's/^-//' -e 's/-$//' | cut -c1-40
}

# ------------------------------------------------------------------ revoke --
if [ -n "$REVOKE" ]; then
  NAME="$(sanitize "$REVOKE")"
  say "looking for bootstrap tokens minted for join:$NAME"
  if [ "$DRY_RUN" = "1" ]; then
    ssh "$SERVER_HOST" "sudo -n k3s kubectl -n kube-system get secrets -o custom-columns=NAME:.metadata.name,DESC:.metadata.annotations.k3s\\.cattle\\.io/description --no-headers" 2>/dev/null | grep "join:$NAME" || true
    exit 0
  fi
  removed=0
  while read -r secret _; do
    [ -n "$secret" ] || continue
    ssh "$SERVER_HOST" "sudo -n k3s kubectl -n kube-system delete secret $secret --ignore-not-found" >/dev/null
    say "revoked $secret"
    removed=$((removed + 1))
  done < <(ssh "$SERVER_HOST" \
    "sudo -n k3s kubectl -n kube-system get secrets -o custom-columns=NAME:.metadata.name,DESC:.metadata.annotations.k3s\\.cattle\\.io/description --no-headers" 2>/dev/null \
    | grep "join:$NAME" || true)
  [ "$removed" -gt 0 ] || say "no live token found for join:$NAME (it may already have been revoked)"
  exit 0
fi

# -------------------------------------------------------------------- mint --
[ -n "$NODE_NAME" ] || die "--name is required (for example --name mikes-laptop)"
NAME="$(sanitize "$NODE_NAME")"
[ -n "$NAME" ] || die "that name has no usable characters"

if [ "$DRY_RUN" = "1" ]; then
  say "would mint an agent token on $SERVER_HOST for join:$NAME (ttl=$K3S_TOKEN_TTL)"
  say "  k3s token create --ttl=$K3S_TOKEN_TTL --usages=agent --description=join:$NAME"
  say "  tailnet key from ${TAILNET_KEY_FILE} (must exist and carry tag:k3s)"
  say "  code prefix ${CODE_PREFIX}, server ${SERVER_URL}"
  exit 0
fi

[ -r "$TAILNET_KEY_FILE" ] || die "missing $TAILNET_KEY_FILE (0600, a Tailscale auth key that carries tag:k3s)"
TAILNET_KEY="$(tr -d ' \n\r' < "$TAILNET_KEY_FILE")"
printf '%s' "$TAILNET_KEY" | grep -Eq '^tskey-[A-Za-z0-9-]+$' \
  || die "$TAILNET_KEY_FILE does not contain a Tailscale auth key"
chmod 600 "$TAILNET_KEY_FILE" 2>/dev/null || true

say "minting an agent token on $SERVER_HOST (ttl=$K3S_TOKEN_TTL)"

TOKEN="$(ssh "$SERVER_HOST" \
  "sudo -n k3s token create --ttl='$K3S_TOKEN_TTL' --usages=agent --description='join:$NAME'" \
  2>/dev/null | tr -d ' \n\r')"
printf '%s' "$TOKEN" | grep -Eq '^[A-Za-z0-9+/=_-]{20,}::[A-Za-z0-9._:-]{8,}$' \
  || die "the control plane did not return a token; check SSH and sudo on $SERVER_HOST"

payload="$(printf '{"v":1,"server":"%s","token":"%s","tailscale_key":"%s","node_name":"%s"}' \
  "$SERVER_URL" "$TOKEN" "$TAILNET_KEY" "$NAME")"
code="$CODE_PREFIX$(printf '%s' "$payload" | base64 -w0 | tr '+/' '-_' | tr -d '=')"

say ""
say "Join code for $NAME — hand this to that machine and nobody else:"
say ""
printf '%s\n' "$code"
say ""
say "They run (Linux):"
say "  curl -fsSL https://k3s.kituwa.com/install.sh | sudo sh -s -- --code '$code'"
say ""
say "macOS:   curl -fsSL https://k3s.kituwa.com/install-macos.sh | sh -s -- --code '$code'"
say "Windows: irm https://k3s.kituwa.com/install.ps1 | iex   # paste the code when asked"
say ""
say "Revoke later with: ./tools/mint-join-code.sh --revoke '$NAME'"
say "The code is a secret. Do not paste it into a public channel."
