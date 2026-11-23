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

VERSION="1.2.0"
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
# 256-colour helper for the logo gradient (cyan -> blue), only when the terminal
# can take it. Piped output stays plain text.
fg256() {
  if [ -t 1 ]; then printf '\033[38;5;%sm%s\033[0m' "$1" "$2"; else printf '%s' "$2"; fi
}

banner() {
  local host arch
  host="$(hostname -s 2>/dev/null || hostname)"
  arch="$(uname -m)"
  printf '\n'
  fg256 51 '  ██╗  ██╗██╗████████╗██╗   ██╗██╗    ██╗ █████╗ '; printf '\n'
  fg256 45 '  ██║ ██╔╝██║╚══██╔══╝██║   ██║██║    ██║██╔══██╗'; printf '\n'
  fg256 39 '  █████╔╝ ██║   ██║   ██║   ██║██║ █╗ ██║███████║'; printf '\n'
  fg256 33 '  ██╔═██╗ ██║   ██║   ██║   ██║██║███╗██║██╔══██║'; printf '\n'
  fg256 27 '  ██║  ██╗██║   ██║   ╚██████╔╝╚███╔███╔╝██║  ██║'; printf '\n'
  fg256 21 '  ╚═╝  ╚═╝╚═╝   ╚═╝    ╚═════╝  ╚══╝╚══╝ ╚═╝  ╚═╝'; printf '\n'
  printf '\n'
  printf '  %s%s\n' "${C_HEAD}KITUWA CLUSTER${C_RESET} ${C_OK}v${VERSION}${C_RESET}" "${C_DIM}  ·  k3s worker, one command${C_RESET}"
  [ "${DRY_RUN:-0}" = "1" ] && run_suffix="  ·  DRY RUN" || run_suffix=""
  printf '  %s\n' "${C_DIM}v${VERSION}  ·  ${host}  ·  $(uname -s)/${arch}${run_suffix}${C_RESET}"
  printf '  %s\n\n' "${C_DIM}───────────────────────────────────────────────${C_RESET}"
}

SPIN_PID=""
spinner_start() {
  [ -t 1 ] || return 0
  SPIN_LABEL="$1"
  SPIN_START=$SECONDS
  (
    i=0
    frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
    while :; do
      printf '\r\033[2K  %s %s… %ss' "${frames[i]}" "$SPIN_LABEL" "$((SECONDS - SPIN_START))"
      i=$(( (i + 1) % 10 ))
      sleep 0.1
    done
  ) &
  SPIN_PID=$!
  return 0
}
spinner_stop() {
  [ -n "$SPIN_PID" ] || return 0
  set +e
  kill "$SPIN_PID" 2>/dev/null || true
  wait "$SPIN_PID" 2>/dev/null || true
  SPIN_PID=""
  printf '\r\033[2K'
  if [ "${1:-0}" = "0" ]; then
    printf '  %sok%s   %s  %s\n' "$C_OK" "$C_RESET" "$SPIN_LABEL" "$((SECONDS - SPIN_START))s"
  else
    printf '  %sfail%s %s  %s\n' "$C_ERR" "$C_RESET" "$SPIN_LABEL" "$((SECONDS - SPIN_START))s"
  fi
  set -e
  return 0
}
# spinner_run "label" command... — runs the command in this shell, spins around it
spinner_run() {
  local label="$1"; shift
  if [ ! -t 1 ]; then "$@"; return $?; fi
  spinner_start "$label"
  local rc=0
  "$@" || rc=$?
  spinner_stop "$rc"
  return "$rc"
}

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

diagnose() {
  say ""
  say "  Collecting the join state — about 20 lines, no secrets."
  local tmp jar tok body id
  tmp="$(mktemp)"
  {
    say "host: $(hostname -s) · $(uname -sr) · $(uname -m)"
    say "k3s: $(/usr/local/bin/k3s --version 2>/dev/null | head -1 || echo 'not installed')"
    say "service: $(systemctl is-active k3s-agent 2>/dev/null || echo unknown) / $(systemctl is-enabled k3s-agent 2>/dev/null || echo unknown)"
    say "--- config (token redacted)"
    sed -e 's/^token:.*/token: <redacted>/' /etc/rancher/k3s/config.yaml 2>/dev/null || say "no config.yaml"
    say "--- token: $(wc -c < /etc/rancher/k3s/token 2>/dev/null || echo 0) bytes, (content withheld)"
    say "--- tailscale: $(tailscale ip -4 2>/dev/null | head -1 || echo none)"
    say "--- api: $(curl -sk -o /dev/null -w '%{http_code}' -m 8 https://100.120.24.106:6443/version 2>/dev/null || echo unreachable)"
    say "--- last agent errors"
    journalctl -u k3s-agent -n 400 --no-pager 2>/dev/null \
      | grep -iE "error|fatal|fail|unauthor|denied|refused|timeout|password" \
      | tail -12 | cut -c1-160 || true
  } > "$tmp" 2>&1

  cat "$tmp"
  say ""
  say "  Sending this to the Kituwa inbox (high priority)…"

  jar="$(mktemp)"
  tok="$(curl -sS -c "$jar" -m 15 https://k3s.kituwa.com/api/v1/forms/k3s-join/token 2>/dev/null \
    | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')"
  if [ -z "$tok" ]; then
    say "  could not get a form token (no network to k3s.kituwa.com?)"
  else
    # JSON-escape the summary without needing python.
    body="$(sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/ /g' "$tmp" | tr -d '\r' | tr '\n' ' ' | cut -c1-3000)"
    body="{\"name\":\"join-diagnostic\",\"email\":\"diagnostic@kituwa.com\",\"machine\":\"diagnostic\",\"reference\":\"diag-$(date +%s)\",\"body\":\"$body\"}"
    code="$(curl -sS -b "$jar" -m 20 -X POST https://k3s.kituwa.com/api/v1/forms/k3s-join \
      -H 'Origin: https://k3s.kituwa.com' -H "X-Kituwa-Form-Token: $tok" \
      -H 'Content-Type: application/json' --data "$body" -o /tmp/kit-diag.out -w '%{http_code}' 2>/dev/null || echo 000)"
    id="$(sed -n 's/.*"id":\([0-9]*\).*/\1/p' /tmp/kit-diag.out 2>/dev/null | head -1)"
    rm -f /tmp/kit-diag.out
    if [ -n "$id" ]; then
      say "  Sent. Reference #$id — read from the inbox, nothing to copy."
    else
      say "  Upload returned HTTP ${code:-000}; the summary above is all I need."
    fi
  fi
  rm -f "$jar" "$tmp"

  if [ -n "${id:-}" ]; then
    say ""
    say "  Sent. Reference #$id — I can read it from the inbox, no need to copy logs."
    return 0
  fi
  say ""
  say "  Could not send automatically. The summary above is all I need — 20 lines."
  return 1
}

# --------------------------------------------------------------- helpers ----
need_root() {
  [ "$(id -u)" -eq 0 ] || die "run this with sudo, for example:
  curl -fsSL https://k3s.kituwa.com/install.sh | sudo sh -s -- --code \"k3s1.…\""
}

have() { command -v "$1" >/dev/null 2>&1; }

run() {
  # run <command...> — obey --dry-run and report the command once.
  printf '  %s%s%s\n' "$C_DIM" "$(printf '%s' "$*" | sed 's/tskey-[A-Za-z0-9_-]*/tskey-…/g')" "$C_RESET"
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
TAILNET_IP=""
NODE_NAME=""
TAINT=""
LABEL=""
ROLE=""
RENEW=""

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
  RENEW="${RENEW:-$(json_field renew)}"
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
  have curl || die "curl is required. Install it first:  sudo apt-get update && sudo apt-get install -y curl"
  [ "$(uname -s)" = "Linux" ] || die "this installer is for Linux. On macOS run install-macos.sh, on Windows run install.ps1"
  [ -d /run/systemd/system ] || die "systemd is required (Alpine and other non-systemd distros are not supported)"
  if [ -f /.dockerenv ] || grep -qaE '(docker|kubepods|lxc)' /proc/1/cgroup 2>/dev/null; then
    [ "$ALLOW_CONTAINER" = "1" ] || die "this looks like a container. k3s needs a real host (a VM is fine); pass --allow-container only if you know it works"
  fi
  if have timedatectl; then
    if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" != "yes" ]; then
      warn "this machine's clock is not NTP-synced; k3s TLS can fail because of it."
      warn "fix it with: sudo timedatectl set-ntp true"
    fi
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
      spinner_start "installing Tailscale"
      tspath="$(mktemp)"
      if ! curl -fsSL --retry 3 https://tailscale.com/install.sh -o "$tspath"; then
        rm -f "$tspath"
        spinner_stop 1
        die "cannot download the Tailscale installer — check this machine's internet connection."
      fi
      if ! sh "$tspath"; then
        rm -f "$tspath"
        spinner_stop 1
        die "Tailscale did not install. On a freshly booted machine the package manager is
often still busy; wait a minute and run the same command again, or install it by hand:
  sudo apt-get update && sudo apt-get install -y tailscale"
      fi
      rm -f "$tspath"
      spinner_stop 0
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
    if ! run tailscale up \
      --authkey="$TAILNET_KEY" \
      --hostname="$NODE_NAME" \
      --advertise-tags="$KITUWA_TAG" \
      --accept-dns=false \
      --accept-routes=false \
      --ssh=false; then
      die "Tailscale rejected this join code's tailnet key — it has expired or was
already used. Get a fresh code at https://k3s.kituwa.com/join/ and run the same
command again; nothing else on this machine needs changing."
    fi
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
    printf 'node-name: "%s"\n' "$NODE_NAME"
    if [ -n "$TAILNET_IP" ]; then printf 'node-ip: %s\n' "$TAILNET_IP"; fi
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

renew_join_code() {
  # Ask the intake for a fresh token using the ticket inside the code. One
  # automatic step: the person never sees the form again.
  [ -n "$RENEW" ] || return 1
  local reply code_payload new_token
  reply="$(curl -sS -m 20 -X POST https://k3s.kituwa.com/api/v1/enroll/renew \
    -H 'Content-Type: application/json' \
    --data "$(printf '{"renew":"%s"}' "$RENEW")" 2>/dev/null)" || return 1
  case "$reply" in *'"ok":true'*) ;; *) return 1 ;; esac
  code_payload="${reply#*\"code\":\"}"
  code_payload="${code_payload%%\"*}"
  [ -n "$code_payload" ] || return 1
  TOKEN=""
  decode_code "$code_payload"
  new_token="$TOKEN"
  [ -n "$new_token" ] || return 1
  umask 077
  printf '%s\n' "$new_token" > "$TOKEN_FILE"
  chmod 0600 "$TOKEN_FILE"
  TOKEN="$new_token"
  return 0
}

install_k3s() {
  if [ -x /usr/local/bin/k3s ] && [ -f /etc/systemd/system/k3s-agent.service ]; then
    step "k3s is already installed on this machine"
    ok "keeping the binary; only the join settings change"
    # A failed earlier attempt leaves the agent retrying with a dead token, and
    # the unit has TimeoutStartSec=0, so a plain `systemctl restart` blocks for
    # ever. Stop it with a bound, clear the stale client credentials, start it
    # without waiting, and let the normal wait loop report progress.
    run timeout 30 systemctl stop k3s-agent || true
    run systemctl reset-failed k3s-agent || true
    if [ -d /var/lib/rancher/k3s/agent ]; then
      # Keep the node password: the server already knows it. Deleting it makes
      # the agent look like a duplicate hostname and it never joins.
      if [ -f /var/lib/rancher/k3s/agent/node-password.txt ]; then
        read -r NODE_PW < /var/lib/rancher/k3s/agent/node-password.txt || true
      fi
      say "  clearing the agent credentials left by the earlier attempt"
      run rm -rf /var/lib/rancher/k3s/agent
      if [ -n "${NODE_PW:-}" ]; then
        run install -d -m 0700 /var/lib/rancher/k3s/agent
        if [ "$DRY_RUN" != "1" ]; then
          printf '%s' "$NODE_PW" > /var/lib/rancher/k3s/agent/node-password.txt
          chmod 0600 /var/lib/rancher/k3s/agent/node-password.txt
        fi
      fi
    fi
    run systemctl start --no-block k3s-agent || true
    return 0
  fi
  step "installing k3s $K3S_VERSION (official installer, sha256-verified)"
  local tmp
  tmp="$(mktemp -d)"
  if [ "$DRY_RUN" = "1" ]; then
    # Show the real animation so a demo looks like the real thing.
    say "  (dry run) would download k3s $K3S_VERSION with a live byte counter"
    spinner_start "downloading and verifying k3s $K3S_VERSION (dry run)"
    sleep 2
    spinner_stop 0
    say "  (dry run) would run the official installer with INSTALL_K3S_VERSION=$K3S_VERSION in agent mode"
    rm -rf "$tmp"
    return 0
  fi
  curl -fsSL "$K3S_INSTALL_URL" -o "$tmp/install.sh" || die "could not download the k3s installer"

  # Download the binary ourselves so the person sees real byte progress instead
  # of a silent spinner for 70 MB. The official installer then skips its own
  # download and uses the verified file. If anything here fails we fall back to
  # the normal path, so this can only add feedback, never remove the install.
  local arch_suffix="" rel_url sum_url got want
  case "$(uname -m)" in
    aarch64|arm64) arch_suffix="-arm64" ;;
    armv7l)        arch_suffix="-armhf" ;;
  esac
  rel_url="https://github.com/k3s-io/k3s/releases/download/$(printf '%s' "$K3S_VERSION" | sed 's/+/%2B/')/k3s${arch_suffix}"
  sum_url="https://github.com/k3s-io/k3s/releases/download/$(printf '%s' "$K3S_VERSION" | sed 's/+/%2B/')/sha256sum-$( [ "$arch_suffix" = "-arm64" ] && echo arm64 || { [ "$arch_suffix" = "-armhf" ] && echo arm || echo amd64; } ).txt"

  if curl -fL --progress-bar --retry 3 --retry-delay 2 -o "$tmp/k3s.bin" "$rel_url"; then
    if curl -fsSL -o "$tmp/sums" "$sum_url" 2>/dev/null; then
      want="$(awk '/ k3s$/ {print $1}' "$tmp/sums" | head -1)"
      got="$(sha256sum "$tmp/k3s.bin" | awk '{print $1}')"
      if [ -z "$want" ] || [ "$want" != "$got" ]; then
        warn "checksum mismatch on the downloaded binary; letting the official installer fetch it"
        rm -f "$tmp/k3s.bin"
      else
        install -m 0755 "$tmp/k3s.bin" /usr/local/bin/k3s
        ok "downloaded and verified ($(du -h "$tmp/k3s.bin" | cut -f1))"
        spinner_start "installing the systemd service"
        rc=0
        INSTALL_K3S_SKIP_DOWNLOAD=true \
        INSTALL_K3S_BIN_DIR=/usr/local/bin \
        INSTALL_K3S_VERSION="$K3S_VERSION" \
        INSTALL_K3S_EXEC="agent" \
        INSTALL_K3S_SYMLINK="skip" \
          sh "$tmp/install.sh" || rc=$?
        spinner_stop "$rc"
        rm -rf "$tmp"
        [ "$rc" = "0" ] || die "the k3s installer failed; see the output above"
        ok "k3s agent installed as a systemd service"
        return 0
      fi
    else
      say "  (no checksum file available; using the official installer's own verification)"
    fi
  else
    warn "direct download failed; using the official installer instead"
  fi

  spinner_start "downloading and verifying k3s $K3S_VERSION (about 70 MB)"
  rc=0
  INSTALL_K3S_VERSION="$K3S_VERSION" \
  INSTALL_K3S_EXEC="agent" \
  INSTALL_K3S_SYMLINK="skip" \
    sh "$tmp/install.sh" || rc=$?
  spinner_stop "$rc"
  rm -rf "$tmp"
  [ "$rc" = "0" ] || die "the k3s installer failed; see the output above"
  ok "k3s agent installed as a systemd service"
}

wait_for_agent() {
  step "starting the agent"
  if [ "$DRY_RUN" = "1" ]; then
    spinner_start "waiting for the service (dry run)"
    sleep 3
    spinner_stop 0
    return 0
  fi

  spinner_start "waiting for k3s-agent.service"

  local waited=0
  while [ "$waited" -lt 240 ]; do
    if systemctl is-active --quiet k3s-agent && ! systemctl is-failed --quiet k3s-agent 2>/dev/null \
       && [ -f /var/lib/rancher/k3s/agent/client-ca.crt ]; then
      spinner_stop 0
      ok "k3s-agent is running"
      break
    fi
    # A dead token fails fast; do not sit here pretending.
    if [ $((waited % 15)) -eq 0 ] && [ "$waited" -gt 0 ]; then
      if [ "$(journalctl -u k3s-agent --since '-2 min' --no-pager 2>/dev/null | grep -ciE 'invalid bearer token|not authorized|node password rejected|already exists|certificate is not yet valid')" -ge 2 ]; then
        if [ "$RENEWED" = "0" ]; then
          RENEWED=1
          spinner_stop 1
          step "the cluster rejected the code — renewing it automatically"
          if renew_join_code; then
            ok "new token written; restarting the agent"
            systemctl start --no-block k3s-agent 2>/dev/null || true
            spinner_start "waiting for k3s-agent.service"
            waited=0
            continue
          fi
          warn "the code could not be renewed automatically"
        else
          spinner_stop 1
        fi
        say ""
        warn "sending the failure to Kituwa automatically…"
        diagnose || true
        die "the cluster rejected this machine's token (401 Not authorized).
The state was uploaded for me to read — you do not need to copy anything."
      fi
    fi
    sleep 1
    waited=$((waited + 1))
  done

  if ! systemctl is-active --quiet k3s-agent; then
    spinner_stop 1
    journalctl -u k3s-agent -n 25 --no-pager >&2 || true
    say ""
    warn "sending the failure to Kituwa automatically…"
    diagnose || true
    die "k3s-agent is not running. The last 25 log lines are above."
  fi
  say ""
  if [ "$(systemctl is-enabled k3s-agent 2>/dev/null)" = "enabled" ]; then
    ok "the agent is up and enabled for every boot"
  else
    warn "the agent is running but not enabled at boot; fixing that now"
    systemctl enable k3s-agent >/dev/null 2>&1 || warn "could not enable it — run: sudo systemctl enable k3s-agent"
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
  say " ${C_OK}${C_HEAD}Connected.${C_RESET} It runs as a service: it starts on boot and keeps"
  say " running after you close this window. Turning the PC off is fine."
  say ""
  say ""
  say " Leave whenever you want, no questions asked:"
  say "   curl -fsSL https://k3s.kituwa.com/install.sh | sudo bash -s -- --uninstall --remove-tailscale"
  printf '%s────────────────────────────────────────────────────────%s\n' "$C_DIM" "$C_RESET"
  say ""
  say "Thank you for donating compute. You can see the cluster at https://k3s.kituwa.com"
}

# ----------------------------------------------------------------- main -----
DRY_RUN=0
ASSUME_YES=0
CHECK_CODE=0
RENEWED=0
DO_DIAGNOSE=0
DO_RESET=0
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
    --diagnose)      DO_DIAGNOSE=1; shift ;;
    --reset)         DO_RESET=1; shift ;;
    --uninstall)     DO_UNINSTALL=1; shift ;;
    --remove-tailscale) REMOVE_TAILSCALE=1; shift ;;
    --yes|-y)        ASSUME_YES=1; shift ;;
    --version|-V)    say "k3s-join installer $VERSION"; exit 0 ;;
    -h|--help)       usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

# Every failure sends its state to the Kituwa inbox, so the script improves from
# real runs instead of guesses. Dry runs and --diagnose itself are excluded.
DIAG_RAN=0
on_exit_report() {
  rc=$?
  if [ "$rc" -ne 0 ] && [ "$DRY_RUN" != "1" ] && [ "$DIAG_RAN" = "0" ] && [ "$DO_DIAGNOSE" = "0" ]; then
    DIAG_RAN=1
    say ""
    warn "sending the failure detail to Kituwa automatically…"
    diagnose || true
  fi
}
trap on_exit_report EXIT

if [ "$DO_DIAGNOSE" = "1" ]; then
  banner
  diagnose
  exit $?
fi

if [ "$DO_UNINSTALL" = "1" ]; then
  uninstall
  exit 0
fi

if [ -z "$CODE" ] && [ -n "$CODE_FILE" ]; then
  [ -r "$CODE_FILE" ] || die "cannot read $CODE_FILE"
  CODE="$(tr -d ' \n\r' < "$CODE_FILE")"
fi

banner

if [ "$DO_RESET" = "1" ]; then
  need_root
  step "resetting the k3s agent on this machine"
  run timeout 30 systemctl stop k3s-agent 2>/dev/null || true
  if [ -x /usr/local/bin/k3s-agent-uninstall.sh ]; then run /usr/local/bin/k3s-agent-uninstall.sh || true; fi
  run rm -rf /var/lib/rancher/k3s /etc/rancher/k3s
  ok "clean slate"
fi

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
