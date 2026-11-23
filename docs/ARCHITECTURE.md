# Architecture

## The cluster

```
                 tailnet (private, WireGuard)
   ┌──────────────────────────┬───────────────────────────┐
   │                          │                           │
┌──▼───────────────┐   ┌──────▼────────┐          ┌───────▼───────┐
│ kituwa-cloud-1   │   │ trinity       │          │ volunteer     │
│ control plane    │◄──┤ worker        ├─────────►│ worker        │
│ 6443 / scheduler │   │ builds, jobs  │  VXLAN   │ containers    │
└──────────────────┘   └───────────────┘          └───────────────┘
        │
        └── serves the published sites (k3s.kituwa.com among them)
```

- **kituwa-cloud-1** is the control plane and the public web host. It answers
  requests whether or not workers are online.
- **trinity** and every volunteer machine are workers: they run pods.
- All node-to-node traffic, including the flannel VXLAN overlay, goes over the
  tailnet interface (`tailscale0`). No cluster port is exposed to the internet.

## Why a tailnet and not a public API

The control plane's node address is its tailnet address and its flannel interface
is `tailscale0`. Flannel VXLAN needs every peer to be able to reach every other
peer directly; on a public network that means opening UDP 8472 to the world and
hoping NAT cooperates. The tailnet gives every node a stable private address and
WireGuard encryption without exposing anything.

The cost is that joining is an invitation, not self-service: a key has to be
issued. That is deliberate — a node is trusted to run workloads.

## Join sequence

```
mint-join-code.sh (operator)
  ├─ ssh kituwa-cloud-1 → k3s token create --usages=agent   (bootstrap token)
  ├─ read ~/lock/tailscale_authkey.txt                       (tagged tailnet key)
  └─ emit k3s1.<base64url(json)>

install.sh (volunteer machine)
  ├─ decode + validate the code
  ├─ preflight: linux, systemd, arch, RAM, disk, kernel modules
  ├─ tailscale up --authkey --advertise-tags=tag:k3s
  ├─ curl https://<server>:6443/cacerts        (reachability, no auth)
  ├─ write /etc/rancher/k3s/{config.yaml,token}  0600
  ├─ get.k3s.io installer, agent mode, sha256-verified, pinned version
  └─ systemctl is-active k3s-agent
```

The agent authenticates once, receives its own kubelet client certificate, and
from then on is an ordinary node. The bootstrap token stays valid for revocation
control rather than expiry: a k3s agent re-reads its token on every start, so an
expired token would produce a node that cannot come back after a reboot. Revoke
by deleting the bootstrap secret (`tools/mint-join-code.sh --revoke NAME`).

## macOS and Windows

k3s only runs on Linux, so both wrappers create a Linux environment and run the
same `install.sh` inside it:

- **macOS**: Lima VM (`kituwa-node`), 2 CPU / 4 GiB / 20 GiB by default,
  containerd disabled so k3s can own the container runtime. No home-directory
  mount. A login item restarts the VM.
- **Windows**: WSL2 with `systemd=true` in `/etc/wsl.conf`, then `install.sh` in
  the distribution. A logon task keeps the VM alive.

This keeps one implementation of the join logic, which is also the one that gets
tested.

## Failure behaviour

The installer is deliberately loud and stops early:

| Condition | Result |
| --- | --- |
| not Linux (on `install.sh`) | stop, point at the right wrapper |
| no systemd / inside a container | stop with the reason |
| < 1.9 GB RAM or < 8 GB free | stop before writing anything |
| tailnet key rejected | stop, node is not modified |
| cluster unreachable | stop before writing config |
| `k3s-agent` not active | print the log, exit non-zero |
| join error in the log | warn with the exact command to read it |

## Serving the page

`site/` is static. `deploy/deploy-site.sh` copies it, the three installers and the
docs to `kituwa-cloud-1` and refreshes the nginx vhost. The page is served from
the host, not from a worker: it stays up when a volunteer machine or trinity is
offline.
