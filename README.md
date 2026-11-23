# k3s-join

Turn an idle machine into a worker node on the Kituwa k3s cluster — one command,
reversible in one command.

- Site: <https://k3s.kituwa.com>
- Source: <https://github.com/CrystalRockMarket/k3s-join>
- License: MIT

This is not a generic "join any Kubernetes cluster" tool. It joins **the Kituwa
cluster**: a worker must be reachable by the cluster over a private tailnet, so
every machine needs a join code. The public form at
<https://k3s.kituwa.com/join/> mints one for you when the cluster has room;
otherwise it lands in the operator's high-priority inbox. Setup and the security
boundary are in [docs/TAILSCALE-SETUP.md](docs/TAILSCALE-SETUP.md).

## Why

Kituwa runs small tools — scrapers, renderers, build pipelines, site backends —
on hardware it owns instead of rented cloud. A worker node turns somebody's spare
desktop into capacity that would otherwise sit idle. Today that is **donating
compute for the cause**; there is no payment, no token and no promise of one.
If the work earns money one day, rewarding contributors is worth doing, but it is
a hope, not a guarantee.

## Quick start

You need a join code from the cluster operator (one line, starts with `k3s1.`).

**Linux** (systemd, amd64/arm64/armv7):

```bash
curl -fsSL https://k3s.kituwa.com/install.sh | sudo bash -s -- --code 'k3s1.…'
```

**macOS** — k3s is Linux-only, so this creates a small Linux VM with Lima and
joins the VM:

```bash
curl -fsSL https://k3s.kituwa.com/install-macos.sh | bash -s -- --code 'k3s1.…'
```

**Windows** — WSL2 with systemd, then the same Linux installer inside it. Run in
an elevated PowerShell:

```powershell
irm https://k3s.kituwa.com/install.ps1 | iex
```

Leave any time:

```bash
curl -fsSL https://k3s.kituwa.com/install.sh | sudo bash -s -- --uninstall --remove-tailscale
```

## Requirements

| Item | Minimum |
| --- | --- |
| CPU | 2 cores, x86-64 / arm64 / armv7 |
| Memory | 2 GB (the installer refuses below ~1.9 GB) |
| Disk | 8 GB free on `/var` |
| OS | Linux with systemd, macOS 13+, Windows 10/11 with WSL2 |
| Network | Can reach the tailnet (the join code carries the key) |

A sleeping laptop is fine: the node leaves the scheduler while it is asleep and
rejoins when it wakes.

## What the installer does

1. **Checks the machine.** Linux, systemd, architecture, RAM, disk. It refuses
   rather than half-installing.
2. **Joins the tailnet.** Installs Tailscale from its official script and joins
   with the key in the join code, tagged `tag:k3s`.
3. **Writes config.** `/etc/rancher/k3s/config.yaml` plus a `0600` token file.
   Nothing is written to your home directory.
4. **Installs k3s.** The official installer, agent mode, version pinned to the
   cluster, binary verified against its published sha256.
5. **Waits for the node.** Confirms `k3s-agent` is active and prints the node
   name; on failure it prints the log and stops.

Read the scripts before running them — they are plain shell and PowerShell:

```bash
curl -fsSL https://k3s.kituwa.com/install.sh | less
```

## What it does not do

- No access to your files. Containers get their own filesystems. On macOS the
  VM is created with no host mounts at all; on Windows the WSL distribution can
  read drives like any WSL shell, but nothing is mounted into a workload.
- No shell for anyone. A worker does not hand out logins.
- No mining, no network scanning, no proxying, no wallet or payment data.
- No hidden binaries: no compiled artifact ships with this project.
- No lock-in: `--uninstall` removes the agent, its config and optionally the
  tailnet login.

## The join code

A code is `k3s1.` followed by base64url-encoded JSON:

```json
{"v":1,"server":"https://<tailnet-ip>:6443","token":"<bootstrap token>","tailscale_key":"<tskey-auth-…>","node_name":"<suggested name>"}
```

- `token` is a k3s bootstrap token created with `--usages=agent`: it can join a
  worker, nothing else.
- `tailscale_key` is a Tailscale auth key that carries `tag:k3s`; the tailnet ACL
  limits that tag to the cluster.
- Codes are per machine and revocable. Details: [docs/JOIN-CODE.md](docs/JOIN-CODE.md).

Never paste a join code into a public issue or chat.

## Operator tools

Minting and revoking codes is documented in [docs/OPERATOR.md](docs/OPERATOR.md).
Short version, from a machine with SSH access to the control plane:

```bash
./tools/mint-join-code.sh --name spare-pc
./tools/mint-join-code.sh --revoke spare-pc
```

## Layout

```
install.sh              Linux installer (the only place install logic lives)
install-macos.sh        macOS wrapper: Lima VM, then install.sh inside it
install.ps1             Windows wrapper: WSL2 + systemd, then install.sh inside
tools/mint-join-code.sh operator tool: issue and revoke join codes
tools/revoke-node.sh    remove a node from the cluster
site/                   the page served at https://k3s.kituwa.com
deploy/                 nginx vhost and the publish script
tests/                  offline tests for the installers
docs/                   architecture, join codes, operator runbook, FAQ
```

## Development

```bash
tests/run-tests.sh          # syntax, shellcheck, dry-run, code parsing
```

The installers are tested offline: `--dry-run` exercises the full flow without
touching the machine, and the join-code parser has fixtures in `tests/`.

## License

MIT — see [LICENSE](LICENSE). Security reports: [SECURITY.md](SECURITY.md).
