# Changelog

## 1.1.0 — 2026-11-24

Earn programme, Premium and the free API.

- `/earn/`: member accounts, node claim, points balance, leaderboard, Premium
  redemption and the member page. Digital only: no physical goods, no shipping
  and no address collection.
- Consumer auth: passkey first, Pocket ID second, password third, with verified
  email; recovery by single-use email link. Operator console uses Pocket ID.
- Points ingest: the host records the difference between published per-node
  totals every five minutes; the worker still computes them.
- Free API: read-only cluster, points and Earn data over HTTPS and MCP. Keys are
  scoped to `read` and revocable. No monthly allowance, no call counter and no
  paid tiers.
- `claim.sh`: one-time node claim run on the machine.
- Site copy, privacy, terms, watchdog, sitemap and registry records updated for
  the programme.
- Worker page: `kituwa-cloud-1` removed from the worker list; live states are
  `working`, `online` and `offline`.
- Premium repriced from 15,000/36,000/120,000 to 7,000/18,000/60,000 points after
  the unit changed to 100 CPU-seconds per point. The Premium page shows what an
  8-core gaming PC earns at 50%, 25% and 10% average load.
- Points audit: 577.31 VivoBook points voided because the reporter counted idle
  CPU and the pull path bypassed the idle/wall cap. Fixed in the agent, reporter,
  intake, pull and ingest; correction recorded in the cluster-points project.

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
