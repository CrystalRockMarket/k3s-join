#!/usr/bin/env bash
# Kituwa Cluster launcher — installs Node if it is missing, then runs the CLI.
#
#   curl -fsSL https://k3s.kituwa.com/cli/run.sh | sudo bash -s -- agent --code 'k3s1.…'
#   curl -fsSL https://k3s.kituwa.com/cli/run.sh | sudo bash -s -- doctor --fix
#
# On a machine without Node this adds it first (distro package, or NodeSource 22).
# The bash installer at /install.sh needs none of this — it is the default path.
set -euo pipefail

need_major=18

have_node() {
  command -v node >/dev/null 2>&1 || return 1
  local v
  v="$(node -v 2>/dev/null | sed 's/^v//')" || return 1
  [ "${v%%.*}" -ge "$need_major" ] 2>/dev/null
}

install_node() {
  say "  Node $need_major+ is not installed; adding it now."
  if command -v apt-get >/dev/null 2>&1; then
    if apt-cache policy nodejs 2>/dev/null | grep -qE 'Candidate: (1[89]|2[0-9])'; then
      say "  using the distribution package"
      DEBIAN_FRONTEND=noninteractive apt-get update -qq || true
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs || true
    fi
    if ! have_node; then
      say "  distribution package too old; using NodeSource 22"
      curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/kituwa-nodesource.sh
      bash /tmp/kituwa-nodesource.sh >/dev/null
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs
      rm -f /tmp/kituwa-nodesource.sh
    fi
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y nodejs
  elif command -v pacman >/dev/null 2>&1; then
    pacman -Sy --noconfirm nodejs npm
  else
    echo "  Could not find a package manager for Node." >&2
    echo "  Use the bash installer instead: curl -fsSL https://k3s.kituwa.com/install.sh | sudo bash -s -- …" >&2
    exit 1
  fi
  have_node || { echo "  Node install did not succeed. Use /install.sh instead." >&2; exit 1; }
}

say() { printf '%s\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
  [ -d /var/tmp ] && work=/var/tmp || work=/tmp
else
  work="${TMPDIR:-/tmp}"
fi

have_node || install_node

cli="$work/kituwa-cluster.mjs"
say "  Fetching the installer…"
curl -fsSL https://k3s.kituwa.com/cli/cluster.mjs -o "$cli"
chmod 0600 "$cli"

say "  Node $(node -v) ready."
exec node "$cli" "$@"
