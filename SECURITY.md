# Security policy

## What this project touches

`install.sh` runs as root on a machine its owner volunteers. It:

- installs Tailscale (official script) and joins the tailnet with the key in the
  join code;
- writes `/etc/kituwa/config.yaml` and `/etc/kituwa/token` (mode 0600);
- runs the official k3s installer in agent mode, pinned to a version and verified
  against its published sha256.

Nothing else. It does not modify your home directory, install a shell account,
open an inbound port, or fetch a binary that is not checked against a published
hash.

## Reporting a vulnerability

Open a private report through GitHub Security Advisories on
<https://github.com/CrystalRockMarket/k3s-join>, or email the address on
<https://kituwa.com/contact>. Please do not open a public issue for a
vulnerability, and never paste a join code, token or tailnet key.

Include the script name and line, what an attacker gains, and the smallest
reproduction you have. Expect an acknowledgement within a few days.

## Threat model in one page

| Asset | Exposure | Control |
| --- | --- | --- |
| k3s bootstrap token | inside the join code | agent-only usage, per-machine, revocable by deleting the bootstrap secret |
| Tailscale auth key | inside the join code | tagged `tag:k3s`, ACL-limited to the cluster, revocable in the tailnet |
| Cluster API | tailnet only, never public | tailnet ACL; no public 6443 listener |
| Volunteer's files | not exposed | containers have their own filesystems; no host mounts |
| Volunteer's network | not exposed | the node makes outbound connections; no inbound forward, no router changes |

A worker node is trusted by the cluster to run workloads, so it can read the
environment of the pods scheduled on it. Do not run secrets on volunteer nodes
that you would not show to the volunteer. That is a scheduling decision made on
the control plane, not by this installer.

## Hardening in the installer

- refuses non-systemd hosts, containers (unless explicitly overridden),
  unsupported architectures, and machines under the memory or disk floor;
- never passes a token on a command line — it goes into a `0600` file that
  `config.yaml` references with `token-file`;
- checks the cluster is reachable before writing anything;
- prints the agent log and exits non-zero instead of claiming success.
