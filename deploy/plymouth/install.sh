#!/usr/bin/env bash
# Install the Kituwa pixel boot splash on this Mint/Ubuntu host.
# Idempotent: safe to re-run after editing the artwork or the theme config.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
THEME_NAME="kituwa-pixels"
THEME_DIR="/usr/share/plymouth/themes/${THEME_NAME}"
ALT="default.plymouth"

say() { printf '  %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

say "installing theme into ${THEME_DIR}"
install -d -m 0755 "${THEME_DIR}"
install -m 0644 "${HERE}/kituwa-pixels.plymouth" "${THEME_DIR}/${THEME_NAME}.plymouth"

# The two-step module picks up its logo from the numbered animation frames.
# One frame = a static mark; the boot progress bar is drawn under it.
install -m 0644 "${HERE}/kituwa-mark-320.png" "${THEME_DIR}/animation-0001.png"
install -m 0644 "${HERE}/kituwa-mark-320.png" "${THEME_DIR}/throbber-0001.png"

say "pointing ${ALT} at ${THEME_NAME}"
update-alternatives --install "${THEME_DIR%/*}/${ALT}" "${ALT}" \
  "${THEME_DIR}/${THEME_NAME}.plymouth" 200 >/dev/null
update-alternatives --set "${ALT}" "${THEME_DIR}/${THEME_NAME}.plymouth"

say "rebuilding initramfs so the splash exists before / is mounted"
update-initramfs -u

say "done: $(readlink -f "/etc/alternatives/${ALT}")"
