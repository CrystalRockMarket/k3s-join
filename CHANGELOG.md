# Changelog

## 1.0.0 — 2026-11-23

First public release.

- `install.sh`: Linux agent installer with preflight checks, tailnet join,
  `0600` config, pinned and sha256-verified k3s, agent health wait, and
  `--uninstall`.
- `install-macos.sh`: Lima VM wrapper, containerd disabled, login item.
- `install.ps1`: WSL2 wrapper that enables systemd and runs the Linux installer,
  with a logon task that keeps the VM alive.
- `tools/mint-join-code.sh`: issue and revoke per-machine join codes.
- `tools/revoke-node.sh`: drain and remove a worker.
- `site/`: the page at <https://k3s.kituwa.com>.
- Offline test suite covering syntax, shellcheck, code parsing, rejection cases
  and secret-free output.
