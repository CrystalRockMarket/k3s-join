# Operator runbook

Everything here runs on trinity (or any host with SSH to the control plane),
never on the machine being joined.

## One-time setup: the tailnet key

Minting needs a Tailscale auth key that carries `tag:k3s`. The tailnet has no
API token on this host, so the key is a file:

```bash
# 0600, one line, reusable and ephemeral, tagged tag:k3s
printf '%s\n' 'tskey-auth-…' > ~/lock/tailscale_authkey.txt
chmod 600 ~/lock/tailscale_authkey.txt
```

Create it in the Tailscale admin console once (Settings → Keys → Generate auth
key, **reusable**, **ephemeral**, tag `tag:k3s`). After that, minting never needs
a browser. If the tailnet later gets an OAuth client with the `auth_keys` scope,
store its credentials instead and mint keys per invitation; that is the next step,
not a requirement today.

Check the ACL admits `tag:k3s` to the cluster ports (6443/tcp and 8472/udp on the
control plane and peer workers). A node that joins untagged is not reachable.

## Issue a code

```bash
cd ~/projects/k3s-join
./tools/mint-join-code.sh --name spare-pc
```

The code goes to stdout, everything else to stderr, so it can be piped into a
password manager. It prints the ready-to-send install line for each OS.

Options:

| Flag | Use |
| --- | --- |
| `--name` | suggested node name, sanitized to a DNS label |
| `--ttl` | bootstrap token lifetime; default `0` (revoke instead of expiring) |
| `--server-url` | override the control plane URL |
| `--tailnet-key-file` | a different auth key file |
| `--dry-run` | show the commands, mint nothing |

## Revoke

```bash
./tools/mint-join-code.sh --revoke spare-pc   # token: new joins fail
./tools/revoke-node.sh spare-pc               # node: pods drain elsewhere
```

Both are idempotent. Revoking a token does not evict a node that already joined,
and deleting a node does not invalidate a token; do both when a machine is gone
for good.

## Check the cluster

```bash
ssh kituwa-cloud-1 'sudo -n k3s kubectl get nodes -o wide'
ssh kituwa-cloud-1 'sudo -n k3s kubectl get pods -A -o wide | grep -i <node>'
```

A worker that is `Ready` shows its tailnet address. A worker that is
`NotReady` after a reboot usually means the token was revoked while the agent
needed it — re-mint and rewrite `/etc/rancher/k3s/token` on that machine.

## Scheduling

Volunteer nodes are ordinary workers. Keep privileged or secret-bearing
workloads on nodes you control:

- label nodes you trust (`kubectl label node trinity trust=owner`) and use
  `nodeAffinity` or `nodeSelector` for pods that read secrets;
- or taint volunteer nodes (`kubectl taint node spare-pc volunteer=true:NoSchedule`)
  and tolerate it only on batch workloads.

The installer does not taint anything: that is a policy decision on the control
plane, and it belongs there.

## Publish the page

```bash
./deploy/deploy-site.sh
```

Copies `site/`, the three installers, the docs and the vhost to
`kituwa-cloud-1`, then reloads nginx. Run `tests/run-tests.sh` first.
