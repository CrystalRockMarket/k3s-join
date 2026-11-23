#!/usr/bin/env bash
# k3s-join — turn this Linux machine into a worker node of the Kituwa k3s cluster.
#
#   https://k3s.kituwa.com
#   https://github.com/CrystalRockMarket/k3s-join   (MIT)
#
# What it does, in order:
#   1. checks the machine really is a Linux host with systemd and enough room;
#   2. joins the Tailscale tailnet with the key inside your join code, so the
#      node is reachable by the cluster and by nothing else;
#   3. writes /etc/rancher/k3s/config.yaml and its 0600 token file;
#   4. runs the official k3s installer (sha256-verified) in agent mode;
#   5. waits for k3s-agent to be active and reports the node name.
#
# It never touches your home directory, never installs a package you did not
# ask for, and can be undone with --uninstall.

# The piped form is `| sudo bash -s --`, but people type `| sudo sh` out of habit.
# dash has no pipefail, and failing with "Illegal option -o pipefail" explains
# nothing. Hand back the exact command instead.
if [ -z "${BASH_VERSION:-}" ]; then
  if [ -n "${0:-}" ] && [ -r "$0" ] && [ "$0" != "sh" ] && [ "$0" != "-sh" ]; then
    exec bash "$0" "$@"
  fi
  printf '%s\n' \
    "This installer must run under bash, not sh:" \
    "  curl -fsSL https://k3s.kituwa.com/install.sh | sudo bash -s -- --code 'k3s1.…'" >&2
  exit 1
fi

set -euo pipefail

VERSION="1.0.0"
DEFAULT_K3S_VERSION="v1.36.5+k3s1"
DEFAULT_SERVER="https://100.120.24.106:6443"
KITUWA_TAG="tag:k3s"
CONFIG_DIR="/etc/rancher/k3s"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
TOKEN_FILE="$CONFIG_DIR/token"
K3S_INSTALL_URL="https://get.k3s.io"
MIN_MEM_MB=1900
MIN_DISK_MB=8000
# Join code payload is a fixed-shape single-line JSON object. Fields are
# parsed by pattern, so mint-join-code.sh must keep exactly this order/shape.
CODE_PREFIX="k3s1."

# ---------------------------------------------------------------- output ----
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_OK=$'\033[32m'
  C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_HEAD=$'\033[1m'
else
  C_RESET=""; C_DIM=""; C_OK=""; C_WARN=""; C_ERR=""; C_HEAD=""
fi
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==>%s %s\n' "$C_HEAD" "$C_RESET" "$*"; }
ok()   { printf '  %sok%s   %s\n' "$C_OK" "$C_RESET" "$*"; }
warn() { printf '  %swarn%s %s\n' "$C_WARN" "$C_RESET" "$*" >&2; }
die()  { printf '\n%serror%s %s\n' "$C_ERR" "$C_RESET" "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
k3s-join — join this machine to the Kituwa k3s cluster as a worker.

Usage:
  sudo sh install.sh --code k3s1.XXXX            # normal install
  sudo sh install.sh --code-file /root/code.txt  # code from a 0600 file
  sudo sh install.sh --uninstall                 # leave the cluster

Options:
  --code CODE            join code issued by the cluster owner
  --code-file PATH       read the join code from a file
  --node-name NAME       node name in the cluster (default: this hostname)
  --k3s-version VER      k3s version (default: value pinned to the cluster)
  --no-tailscale         machine is already on the tailnet with tag:k3s
  --dry-run              print every action, change nothing
  --uninstall            stop the agent and remove k3s state
  --remove-tailscale     with --uninstall, also log this machine out of the tailnet
  --yes                  do not ask for confirmation
  --check-code           verify a join code and print what it contains
  -h, --help             this text
  -V, --version          installer version

Environment (used when the matching flag is absent):
  KITUWA_JOIN_CODE, KITUWA_CODE_FILE, KITUWA_NODE_NAME, KITUWA_K3S_VERSION
USAGE
}

# --------------------------------------------------------------- helpers ----
need_root() {
  [ "$(id -u)" -eq 0 ] || die "run this with sudo, for example:
  curl -fsSL https://k3s.kituwa.com/install.sh | sudo sh -s -- --code \"k3s1.…\""
}

have() { command -v "$1" >/dev/null 2>&1; }

run() {
  # run <command...> — obey --dry-run and report the command once.
  printf '  %s%s%s\n' "$C_DIM" "$*" "$C_RESET"
  [ "$DRY_RUN" = "1" ] && return 0
  "$@"
}

confirm() {
  [ "$ASSUME_YES" = "1" ] && return 0
  if [ ! -t 0 ]; then
    say "  (no terminal to ask on: continuing, this is what you piped in)"
    return 0
  fi
  printf '%s [y/N] ' "$1"
  read -r answer
  case "$answer" in y|Y|yes|YES) return 0 ;; *) say "stopped."; exit 1 ;; esac
}

sanitize_name() {
  # A k8s node name must be a lowercase RFC 1123 label: a-z 0-9 - and start/end
  # alphanumeric, 63 characters max.
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -e 's/[^a-z0-9-]/-/g' -e 's/--*/-/g' -e 's/^-//' -e 's/-$//' \
    | cut -c1-63
}

# ----------------------------------------------------------- join code ------
CODE=""
SERVER=""
TOKEN=""
TAILNET_KEY=""
NODE_NAME=""
TAINT=""
LABEL=""
ROLE=""

decode_code() {
  local raw="$1" payload
  case "$raw" in
    "$CODE_PREFIX"*) payload="${raw#"$CODE_PREFIX"}" ;;
    *) die "that is not a join code (expected it to start with ${CODE_PREFIX})" ;;
  esac
  payload="${payload# }"
  payload="$(printf '%s' "$payload" | tr -d ' \n\r\t')"
  payload="$(printf '%s' "$payload" | tr '_-' '/+')"
  case $((${#payload} % 4)) in
    2) payload="$payload==" ;;
    3) payload="$payload=" ;;
    1) die "join code is truncated" ;;
  esac
  payload="$(printf '%s' "$payload" | base64 -d 2>/dev/null)" \
    || die "join code is not valid base64 — copy it again"
  CODE_JSON="$payload"
  json_field() {
    printf '%s' "$CODE_JSON" | sed -n "s/.*\"$1\":\"\\([^\"]*\\)\".*/\\1/p"
  }
  SERVER="${SERVER:-$(json_field server)}"
  TOKEN="${TOKEN:-$(json_field token)}"
  TAILNET_KEY="${TAILNET_KEY:-$(json_field tailscale_key)}"
  node_hint="$(json_field node_name)"
  TAINT="${TAINT:-$(json_field taint)}"
  LABEL="${LABEL:-$(json_field label)}"
  ROLE="${ROLE:-$(json_field role)}"
  if [ -z "$NODE_NAME" ] && [ -n "$node_hint" ]; then NODE_NAME="$node_hint"; fi
}

validate_inputs() {
  [ -n "$SERVER" ] || die "no cluster server address; pass --code or --server"
  printf '%s' "$SERVER" | grep -Eq '^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$' \
    || die "server address must be https://host[:port], got: $SERVER"
  [ -n "$TOKEN" ] || die "no cluster token; pass --code or --token"
  printf '%s' "$TOKEN" | grep -Eq '^[A-Za-z0-9+/=_-]{20,}::[A-Za-z0-9._:-]{8,}$' \
    || die "that token does not look like a k3s join token"
  if [ "$USE_TAILSCALE" = "1" ]; then
    [ -n "$TAILNET_KEY" ] || die "this join code has no tailnet key; rerun with --tailnet-key, or pass --no-tailscale if this machine is already tagged tag:k3s"
    printf '%s' "$TAILNET_KEY" | grep -Eq '^tskey-[A-Za-z0-9-]+$' \
      || die "that tailnet key does not look like a Tailscale auth key (tskey-…)"
  fi
}

# ----------------------------------------------------------- preflight ------
preflight() {
  step "checking this machine"
  [ "$(uname -s)" = "Linux" ] || die "this installer is for Linux. On macOS run install-macos.sh, on Windows run install.ps1"
  [ -d /run/systemd/system ] || die "systemd is required (Alpine and other non-systemd distros are not supported)"
  if [ -f /.dockerenv ] || grep -qaE '(docker|kubepods|lxc)' /proc/1/cgroup 2>/dev/null; then
    [ "$ALLOW_CONTAINER" = "1" ] || die "this looks like a container. k3s needs a real host (a VM is fine); pass --allow-container only if you know it works"
  fi
  for mod in br_netfilter overlay; do
    modprobe "$mod" 2>/dev/null || warn "kernel module $mod is not loadable; k3s may fail to start"
  done
  ok "linux, systemd, kernel modules checked"

  local mem_mb disk_mb arch
  mem_mb="$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)"
  disk_mb="$(df -Pm /var 2>/dev/null | awk 'NR==2 {print $4}')"
  arch="$(uname -m)"
  [ "$mem_mb" -ge "$MIN_MEM_MB" ] || die "only ${mem_mb} MB of RAM; ${MIN_MEM_MB} MB is the minimum for a worker"
  [ "${disk_mb:-0}" -ge "$MIN_DISK_MB" ] || die "only ${disk_mb:-0} MB free on /var; ${MIN_DISK_MB} MB is the minimum"
  case "$arch" in
    x86_64|amd64|aarch64|arm64|armv7l) ok "arch $arch, ${mem_mb} MB RAM, ${disk_mb} MB free on /var" ;;
    *) die "unsupported architecture: $arch (k3s supports amd64, arm64 and armv7)" ;;
  esac
}

# ----------------------------------------------------------- tailscale ------
tailnet_ipv4() { tailscale ip -4 2>/dev/null | head -1; }

ensure_tailscale() {
  [ "$USE_TAILSCALE" = "1" ] || { ok "tailnet step skipped (--no-tailscale)"; return 0; }
  step "joining the tailnet"
  if ! have tailscale; then
    say "  installing Tailscale from its official script"
    if [ "$DRY_RUN" = "1" ]; then
      run sh -c 'curl -fsSL https://tailscale.com/install.sh | sh'
    else
      curl -fsSL https://tailscale.com/install.sh -o /tmp/kituwa-tailscale-install.sh
      sh /tmp/kituwa-tailscale-install.sh >/dev/null
      rm -f /tmp/kituwa-tailscale-install.sh
      have tailscale || die "Tailscale install finished but the tailscale command is missing"
    fi
    ok "tailscale installed"
  else
    ok "tailscale already installed"
  fi

  local ip
  ip="$(tailnet_ipv4 || true)"
  if [ -z "$ip" ]; then
    say "  logging this machine in to the tailnet"
    run tailscale up \
      --authkey="$TAILNET_KEY" \
      --hostname="$NODE_NAME" \
      --advertise-tags="$KITUWA_TAG" \
      --accept-dns=false \
      --accept-routes=false \
      --ssh=false
    local i=0
    while [ -z "$ip" ] && [ "$i" -lt 30 ]; do
      sleep 1
      ip="$(tailnet_ipv4 || true)"
      i=$((i + 1))
    done
  fi
  [ -n "$ip" ] || die "this machine did not get a tailnet address; check that the join code's tailnet key is still valid"
  TAILNET_IP="$ip"
  ok "tailnet address $TAILNET_IP"

  if tailscale status --json 2>/dev/null | grep -q "\"$KITUWA_TAG\""; then
    ok "node carries $KITUWA_TAG"
  else
    warn "node is not tagged $KITUWA_TAG yet; the control plane may be unable to reach it. The tag comes from the join code's key."
  fi
}

check_server() {
  step "reaching the cluster"
  # The API server certificate is signed by the cluster CA, so a plain curl
  # cannot verify it. This probe only asks "is something answering TLS on this
  # address". The real trust anchor is the CA hash embedded in the join token,
  # which k3s verifies when the agent connects.
  local url="${SERVER%/}" code
  code="$(curl -skS -o /dev/null -w '%{http_code}' --max-time 10 "$url/version" 2>/dev/null || true)"
  case "$code" in
    200|401|403) ok "$url answers (HTTP $code)" ;;
    *) die "cannot reach $url (got '${code:-no answer}'). A worker must be on the tailnet, so check the tailnet key and that Tailscale is connected." ;;
  esac
}

# --------------------------------------------------------------- k3s --------
write_config() {
  step "writing k3s agent configuration"
  run install -d -m 0700 "$CONFIG_DIR"
  if [ "$DRY_RUN" = "1" ]; then
    say "  (dry run) would write $TOKEN_FILE (0600) and $CONFIG_FILE (0600)"
    return 0
  fi
  umask 077
  printf '%s\n' "$TOKEN" > "$TOKEN_FILE"
  chmod 0600 "$TOKEN_FILE"
  {
    printf 'server: %s\n' "${SERVER%/}"
    printf 'token-file: %s\n' "$TOKEN_FILE"
    printf 'node-name: %s\n' "$NODE_NAME"
    printf 'node-ip: %s\n' "$TAILNET_IP"
    if [ "$USE_TAILSCALE" = "1" ]; then
      printf 'flannel-iface: tailscale0\n'
    fi
    # A volunteer node never runs secret-bearing workloads: it joins tainted and
    # labelled, so only workloads that explicitly tolerate the taint land on it.
    if [ -n "$TAINT" ]; then
      printf 'node-taint:\n  - %s\n' "$TAINT"
    fi
    if [ -n "$LABEL" ]; then
      printf 'node-label:\n  - %s\n' "$LABEL"
    fi
  } > "$CONFIG_FILE"
  chmod 0600 "$CONFIG_FILE"
  ok "$CONFIG_FILE and $TOKEN_FILE written 0600"
}

install_k3s() {
  step "installing k3s $K3S_VERSION (official installer, sha256-verified)"
  local tmp
  tmp="$(mktemp -d)"
  if [ "$DRY_RUN" = "1" ]; then
    say "  (dry run) would fetch $K3S_INSTALL_URL and run it with INSTALL_K3S_VERSION=$K3S_VERSION in agent mode"
    rm -rf "$tmp"
    return 0
  fi
  curl -fsSL "$K3S_INSTALL_URL" -o "$tmp/install.sh" || die "could not download the k3s installer"
  INSTALL_K3S_VERSION="$K3S_VERSION" \
  INSTALL_K3S_EXEC="agent" \
  INSTALL_K3S_SYMLINK="skip" \
    sh "$tmp/install.sh"
  rm -rf "$tmp"
  ok "k3s agent installed"
}

wait_for_agent() {
  step "waiting for the agent to start"
  if [ "$DRY_RUN" = "1" ]; then say "  (dry run) would wait for k3s-agent.service"; return 0; fi
  local i=0
  while [ "$i" -lt 90 ]; do
    if systemctl is-active --quiet k3s-agent; then
      ok "k3s-agent is active"
      break
    fi
    sleep 2; i=$((i + 2))
  done
  if ! systemctl is-active --quiet k3s-agent; then
    printf '\n' >&2
    journalctl -u k3s-agent -n 40 --no-pager >&2 || true
    die "k3s-agent did not start; the last 40 log lines are above"
  fi
  if journalctl -u k3s-agent -n 200 --no-pager 2>/dev/null | grep -qiE 'level=fatal|failed to (join|register)|401 unauthorized'; then
    warn "the agent log mentions a join failure; run: sudo journalctl -u k3s-agent -n 60"
  else
    ok "agent joined without a reported error"
  fi
}

# ------------------------------------------------------------ uninstall -----
uninstall() {
  need_root
  step "removing k3s agent"
  if [ -x /usr/local/bin/k3s-agent-uninstall.sh ]; then
    run /usr/local/bin/k3s-agent-uninstall.sh
  elif [ -x /usr/local/bin/k3s-uninstall.sh ]; then
    die "this machine runs a k3s *server*, not an agent; refusing to uninstall"
  else
    warn "no k3s uninstall script found; cleaning up what is left"
  fi
  run rm -rf "$CONFIG_DIR"
  if [ "$REMOVE_TAILSCALE" = "1" ]; then
    if have tailscale; then
      step "logging this machine out of the tailnet"
      run tailscale logout || warn "tailscale logout failed"
    fi
  fi
  say ""
  say "This machine is no longer part of the cluster."
  if [ "$REMOVE_TAILSCALE" != "1" ] && have tailscale; then
    say "It is still on the tailnet. Add --remove-tailscale to undo that too."
  fi
}

summary() {
  say ""
  printf '%s────────────────────────────────────────────────────────%s\n' "$C_DIM" "$C_RESET"
  if [ -n "$TAINT" ]; then
    say "   mode      volunteer (idle-only, tainted: $TAINT)"
  fi
  if [ "$DRY_RUN" = "1" ]; then
    say " Dry run: this machine would now be a worker in the Kituwa cluster."
  else
    say " This machine is now a worker in the Kituwa cluster."
  fi
  say ""
  say "   node      $NODE_NAME"
  say "   server    ${SERVER%/}"
  say "   version   $K3S_VERSION"
  say ""
  say " Check it any time:"
  say "   systemctl status k3s-agent"
  say "   sudo journalctl -u k3s-agent -f"
  say ""
  say " Leave whenever you want, no questions asked:"
  say "   curl -fsSL https://k3s.kituwa.com/install.sh | sudo sh -s -- --uninstall --remove-tailscale"
  printf '%s────────────────────────────────────────────────────────%s\n' "$C_DIM" "$C_RESET"
  say ""
  say "Thank you for donating compute. You can see the cluster at https://k3s.kituwa.com"
}

# ----------------------------------------------------------------- main -----
DRY_RUN=0
ASSUME_YES=0
CHECK_CODE=0
USE_TAILSCALE=1
ALLOW_CONTAINER=0
DO_UNINSTALL=0
REMOVE_TAILSCALE=0
K3S_VERSION="${KITUWA_K3S_VERSION:-$DEFAULT_K3S_VERSION}"
CODE="${KITUWA_JOIN_CODE:-}"
CODE_FILE="${KITUWA_CODE_FILE:-}"
SERVER=""
TOKEN=""
TAILNET_KEY=""
NODE_NAME="${KITUWA_NODE_NAME:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --code)          CODE="${2:?--code needs a value}"; shift 2 ;;
    --code=*)        CODE="${1#*=}"; shift ;;
    --code-file)     CODE_FILE="${2:?--code-file needs a path}"; shift 2 ;;
    --code-file=*)   CODE_FILE="${1#*=}"; shift ;;
    --server)        SERVER="${2:?--server needs a value}"; shift 2 ;;
    --server=*)      SERVER="${1#*=}"; shift ;;
    --token)         TOKEN="${2:?--token needs a value}"; shift 2 ;;
    --tailnet-key)   TAILNET_KEY="${2:?--tailnet-key needs a value}"; shift 2 ;;
    --tailnet-key-file) TAILNET_KEY="$(cat "${2:?}" 2>/dev/null || true)"; shift 2 ;;
    --node-name)     NODE_NAME="${2:?--node-name needs a value}"; shift 2 ;;
    --node-name=*)   NODE_NAME="${1#*=}"; shift ;;
    --k3s-version)   K3S_VERSION="${2:?--k3s-version needs a value}"; shift 2 ;;
    --k3s-version=*) K3S_VERSION="${1#*=}"; shift ;;
    --no-tailscale)  USE_TAILSCALE=0; shift ;;
    --allow-container) ALLOW_CONTAINER=1; shift ;;
    --dry-run)       DRY_RUN=1; shift ;;
    --check-code)    CHECK_CODE=1; shift ;;
    --uninstall)     DO_UNINSTALL=1; shift ;;
    --remove-tailscale) REMOVE_TAILSCALE=1; shift ;;
    --yes|-y)        ASSUME_YES=1; shift ;;
    --version|-V)    say "k3s-join installer $VERSION"; exit 0 ;;
    -h|--help)       usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

if [ "$DO_UNINSTALL" = "1" ]; then
  uninstall
  exit 0
fi

if [ -z "$CODE" ] && [ -n "$CODE_FILE" ]; then
  [ -r "$CODE_FILE" ] || die "cannot read $CODE_FILE"
  CODE="$(tr -d ' \n\r' < "$CODE_FILE")"
fi

say ""
say "k3s-join $VERSION — add this machine to the Kituwa cluster as a worker"
say "source: https://github.com/CrystalRockMarket/k3s-join (MIT)"

if [ -n "$CODE" ]; then
  decode_code "$CODE"
else
  SERVER="${SERVER:-$DEFAULT_SERVER}"
fi

NODE_NAME="${NODE_NAME:-$(sanitize_name "$(hostname -s 2>/dev/null || hostname)")}"
NODE_NAME="$(sanitize_name "$NODE_NAME")"
[ -n "$NODE_NAME" ] || die "could not derive a node name; pass --node-name"

validate_inputs

if [ "$CHECK_CODE" = "1" ]; then
  masked="$(printf '%s' "$TOKEN" | sed 's/^\(.\{6\}\).*/\1…/')"
  say ""
  say "join code is valid"
  say "  server        ${SERVER%/}"
  say "  node name     $NODE_NAME"
  say "  token         ${masked}  (agent usage only)"
  if [ "$USE_TAILSCALE" = "1" ]; then
    say "  tailnet key   present (tskey-…)"
  else
    say "  tailnet key   not used (--no-tailscale)"
  fi
  if [ -n "$TAINT" ] || [ -n "$LABEL" ]; then
    say "  mode          volunteer (taint: ${TAINT:-none}, label: ${LABEL:-none})"
  fi
  say ""
  say "This only reads the code; nothing was installed or changed."
  exit 0
fi

[ "$DRY_RUN" = "1" ] || need_root
preflight
say ""
say "  This will install k3s $K3S_VERSION as an agent named ${C_HEAD}$NODE_NAME${C_RESET}."
say "  It joins the tailnet, takes container workloads from the cluster, and"
say "  leaves your files, accounts and network alone."
confirm "Continue?"

ensure_tailscale
check_server
write_config
install_k3s
wait_for_agent
summary
