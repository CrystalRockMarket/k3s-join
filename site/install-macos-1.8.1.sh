#!/usr/bin/env bash
# k3s-join for macOS — k3s is Linux-only, so this creates a small Linux VM
# with Lima and joins *that* to the Kituwa cluster as a worker. The VM keeps no
# copy of your home directory; it only runs cluster workloads.
#
#   curl -fsSL https://k3s.kituwa.com/install-macos.sh | sh -s -- --code 'k3s1.…'
#
# Undo:  ./install-macos.sh --uninstall   (deletes the VM, leaves Lima installed)

if [ -z "${BASH_VERSION:-}" ]; then
  if [ -n "${0:-}" ] && [ -r "$0" ] && [ "$0" != "sh" ] && [ "$0" != "-sh" ]; then
    exec bash "$0" "$@"
  fi
  printf '%s\n' \
    "This installer must run under bash, not sh:" \
    "  curl -fsSL https://k3s.kituwa.com/install-macos.sh | bash -s -- --code 'k3s1.…'" >&2
  exit 1
fi

set -euo pipefail

VERSION="1.0.0"
VM_NAME="${KITUWA_VM_NAME:-kituwa-node}"
VM_CPUS="${KITUWA_VM_CPUS:-2}"
VM_MEMORY="${KITUWA_VM_MEMORY:-4}"
VM_DISK="${KITUWA_VM_DISK:-20}"
INSTALL_URL="https://k3s.kituwa.com/install.sh"
LIMA_VERSION_FALLBACK="1.1.1"
DO_UNINSTALL=0
DO_AUTOSTART=1
CODE=""
DRY_RUN=0

say()  { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
ok()   { printf '  ok   %s\n' "$*"; }
warn() { printf '  warn %s\n' "$*" >&2; }
die()  { printf '\nerror %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --code)         CODE="${2:?}"; shift 2 ;;
    --code=*)       CODE="${1#*=}"; shift ;;
    --code-file)    CODE="$(tr -d ' \n\r' < "${2:?}")"; shift 2 ;;
    --uninstall)    DO_UNINSTALL=1; shift ;;
    --no-autostart) DO_AUTOSTART=0; shift ;;
    --dry-run)      DRY_RUN=1; shift ;;
    -h|--help)
      cat <<'USAGE'
k3s-join for macOS

  --code CODE        join code from the cluster owner
  --code-file PATH   read the code from a file
  --uninstall        delete the kituwa-node VM
  --no-autostart     do not add a login item that restarts the VM
  --dry-run          show what would happen
USAGE
      exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[ "$(uname -s)" = "Darwin" ] || die "this script is for macOS; on Linux run install.sh, on Windows run install.ps1"
ARCH="$(uname -m)"
case "$ARCH" in arm64|x86_64) ;; *) die "unsupported architecture: $ARCH" ;; esac

# -------------------------------------------------------------------- lima --
ensure_lima() {
  step "checking the Linux VM layer (Lima)"
  LIMACTL="${LIMACTL:-limactl}"
  if have limactl; then ok "limactl found"; LIMACTL="$(command -v limactl)"; return 0; fi
  if have brew; then
    say "  installing Lima with Homebrew"
    [ "$DRY_RUN" = "1" ] || brew install lima
    LIMACTL="$(command -v limactl || printf '/opt/homebrew/bin/limactl')"
    return 0
  fi
  local dir="$HOME/.kituwa/lima" ver url
  ver="$(curl -fsSL https://api.github.com/repos/lima-vm/lima/releases/latest 2>/dev/null \
    | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' | head -1)"
  ver="${ver:-$LIMA_VERSION_FALLBACK}"
  url="https://github.com/lima-vm/lima/releases/download/v${ver}/lima-${ver}-Darwin-${ARCH}.tar.gz"
  say "  downloading Lima ${ver} to ${dir}"
  if [ "$DRY_RUN" = "1" ]; then say "  (dry run) would fetch $url"; return 0; fi
  mkdir -p "$dir"
  curl -fsSL "$url" -o "$dir/lima.tar.gz" || die "could not download Lima from $url (install Homebrew and run: brew install lima)"
  tar -xzf "$dir/lima.tar.gz" -C "$dir"
  rm -f "$dir/lima.tar.gz"
  LIMACTL="$dir/bin/limactl"
  [ -x "$LIMACTL" ] || die "Lima unpacked but limactl is missing"
  ok "Lima ${ver} installed in $dir"
}

# --------------------------------------------------------------------- vm ---
ensure_vm() {
  step "checking the Linux VM"
  if "$LIMACTL" list --format '{{.Name}}' 2>/dev/null | grep -qx "$VM_NAME"; then
    ok "VM $VM_NAME already exists"
    if [ "$("$LIMACTL" list --format '{{.Name}} {{.Status}}' | awk -v n="$VM_NAME" '$1==n {print $2}')" != "Running" ]; then
      say "  starting VM $VM_NAME"
      [ "$DRY_RUN" = "1" ] || "$LIMACTL" start "$VM_NAME" --tty=false
    fi
    return 0
  fi
  say "  creating VM $VM_NAME (${VM_CPUS} CPU, ${VM_MEMORY} GiB, ${VM_DISK} GiB)"
  if [ "$DRY_RUN" = "1" ]; then
    say "  (dry run) would create the VM from template://ubuntu with containerd disabled"
    return 0
  fi
  "$LIMACTL" start \
    --name "$VM_NAME" \
    --tty=false \
    --cpus "$VM_CPUS" \
    --memory "$VM_MEMORY" \
    --disk "$VM_DISK" \
    --containerd none \
    --set '.mounts = []' \
    template://ubuntu
  ok "VM $VM_NAME created"
}

install_in_vm() {
  step "installing the cluster agent inside the VM"
  if [ "$DRY_RUN" = "1" ]; then say "  (dry run) would run $INSTALL_URL inside the VM"; return 0; fi
  printf '%s' "$CODE" | "$LIMACTL" shell "$VM_NAME" -- \
    sudo sh -c 'umask 077; mkdir -p /root/.kituwa; cat > /root/.kituwa/code; chmod 600 /root/.kituwa/code'
  curl -fsSL "$INSTALL_URL" | "$LIMACTL" shell "$VM_NAME" -- \
    sudo sh -s -- --code-file /root/.kituwa/code --yes --node-name "$(scutil --get ComputerName 2>/dev/null | tr -cd '[:alnum:]-' | cut -c1-40 || true)mac"
  "$LIMACTL" shell "$VM_NAME" -- sudo rm -f /root/.kituwa/code
  ok "agent installed"
}

autostart() {
  [ "$DO_AUTOSTART" = "1" ] || return 0
  step "keeping the node up after login"
  local plist="$HOME/Library/LaunchAgents/com.kituwa.k3s-node.plist"
  local limactl_path="$LIMACTL"
  mkdir -p "$(dirname "$plist")"
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.kituwa.k3s-node</string>
  <key>ProgramArguments</key>
  <array>
    <string>${limactl_path}</string>
    <string>start</string>
    <string>${VM_NAME}</string>
    <string>--tty=false</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/kituwa-k3s-node.log</string>
  <key>StandardErrorPath</key><string>/tmp/kituwa-k3s-node.log</string>
</dict>
</plist>
PLIST
  launchctl unload "$plist" >/dev/null 2>&1 || true
  launchctl load "$plist" >/dev/null 2>&1 || warn "could not load the login item; the VM still runs until you log out"
  ok "login item com.kituwa.k3s-node installed"
}

uninstall_vm() {
  step "deleting the VM"
  if ! "$LIMACTL" list --format '{{.Name}}' 2>/dev/null | grep -qx "$VM_NAME"; then
    say "  no VM named $VM_NAME"; return 0
  fi
  "$LIMACTL" delete "$VM_NAME" --force
  launchctl unload "$HOME/Library/LaunchAgents/com.kituwa.k3s-node.plist" >/dev/null 2>&1 || true
  rm -f "$HOME/Library/LaunchAgents/com.kituwa.k3s-node.plist"
  say "VM $VM_NAME deleted. Lima itself is still installed; remove it with: brew uninstall lima"
}

say ""
say "k3s-join $VERSION for macOS — Linux VM, cluster worker"
say "source: https://github.com/CrystalRockMarket/k3s-join (MIT)"

if [ "$DO_UNINSTALL" = "1" ]; then
  LIMACTL="${LIMACTL:-limactl}"
  command -v "$LIMACTL" >/dev/null 2>&1 || "$LIMACTL" --version >/dev/null 2>&1 || {
    say "Lima is not installed, so there is no VM to delete."; exit 0; }
  uninstall_vm
  exit 0
fi

ensure_lima

if [ -z "$CODE" ]; then
  if [ -t 0 ]; then
    printf 'Paste your join code (it starts with k3s1.): '
    read -r CODE
  else
    die "no join code. Use --code, or ask the cluster owner for one."
  fi
fi
case "$CODE" in k3s1.*) ;; *) die "that does not look like a join code" ;; esac

ensure_vm
install_in_vm
autostart

say ""
say "────────────────────────────────────────────────────────"
say " This Mac now runs a Linux worker for the Kituwa cluster."
say ""
say "   VM        $VM_NAME (Lima)"
say "   check     limactl shell $VM_NAME -- sudo systemctl status k3s-agent"
say "   leave     curl -fsSL https://k3s.kituwa.com/install-macos.sh | bash -s -- --uninstall"
say "────────────────────────────────────────────────────────"
say "Thank you for donating compute. https://k3s.kituwa.com"
