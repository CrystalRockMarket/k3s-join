# Tailscale setup for open onboarding

Everything about the cluster runs on a private tailnet: a worker joins by getting a
device on it, tagged for the cluster. To hand out join codes automatically, the
messages service needs permission to mint **one** tailnet key per request.

Two ways to grant that. **Option A is the one to use.**

## Option A — an OAuth client (recommended)

An OAuth client is a scoped robot: it can mint device keys and nothing else, it
never expires on you, and every key it makes is tagged.

1. Open <https://login.tailscale.com/admin/settings/oauth>.
2. **Generate OAuth client…**
3. Name: `kituwa-join-mint`.
4. Scopes: tick **`auth_keys`** (write). Leave every other scope off.
5. Tags: add **`tag:k3s`**. The client may only mint keys carrying that tag.
6. Create, then copy the **Client ID** and **Client secret** (the secret is shown
   once).

Put them on the host, where the intake runs, without either value touching a shell
history or a log:

```bash
ssh kituwa-cloud-1 'sudo -n /home/ubuntu/services/kituwa-messages/tools/set-tailscale-cred.sh' < <(printf '%s\n%s\n' 'CLIENT_ID' 'CLIENT_SECRET')
```

The helper writes `TS_CLIENT_ID` / `TS_CLIENT_SECRET` into the service `.env`
(mode 0600), restarts `kituwa-messages` and prints only `ok`.

Check it:

```bash
curl -s https://k3s.kituwa.com/api/v1/enroll/status
# {"ok":true,"enabled":true,"active_nodes":0,"capacity":25,"remaining":25,...}
```

## Option B — an API access token

Simpler, long-lived, and broader than it needs to be. Only if OAuth is inconvenient:

1. <https://login.tailscale.com/admin/settings/keys> → **Generate access token**.
2. Save it as `TS_API_KEY` in the same `.env` (0600) and restart the unit.

An API token belongs to a user and can do what that user can do. An OAuth client
with `auth_keys` can only make device keys. Prefer A.

## The ACL — the security boundary

One file, not one rule. <https://login.tailscale.com/admin/acls> holds the whole
tailnet policy: `tagOwners` (who may create a tag), `grants`/`acls` (who may reach
what), plus optional `hosts` and `nodeAttrs`. You **edit that file**; a separate
"rule" pasted somewhere else does nothing.

### The trap to understand first

A new tailnet starts with:

```json
{ "acls": [ { "action": "accept", "src": ["*"], "dst": ["*:*"] } ] }
```

`src: "*"` includes tagged machines. While that entry exists, **every grant you
add is ignored** — a volunteer laptop can already reach Gitea, Grafana, your
desktop and the mail server. Scoping `tag:k3s` therefore means replacing the
wide-open entry, not adding to it. In Tailscale there is no "deny" rule: access
exists only where a grant allows it, so removing the broad grant *is* the deny.

### This estate's shape (why the policy below has four grants)

| device | tag | role |
| --- | --- | --- |
| kituwa-cloud-1 | `tag:k3s` | cluster control plane |
| trinity | *untagged* (owner's user device) | cluster worker + everything else |
| el-COMMS0001 | `tag:k3s`, `tag:server` | offline, another cluster node |
| phone, laptop | *untagged* | people |

So the cluster is half tagged and half user-owned. The policy must keep
`tag:k3s ↔ member` on the cluster ports, or the host and trinity stop seeing each
other and the cluster falls over.

### The policy to paste

Back up what is there first (copy it into a file, or rely on the editor's version
history, below), then replace the contents with:

```jsonc
{
  "tagOwners": {
    "tag:k3s": ["autogroup:admin"]
  },
  "grants": [
    // People keep exactly what they have today: their own devices, each other.
    { "src": ["autogroup:member"], "dst": ["autogroup:member"], "ip": ["*"] },

    // A person may reach the cluster host for admin: SSH, kubelet, API, VXLAN.
    // This also carries the reverse SSH tunnel the ntfy relay uses (port 22).
    { "src": ["autogroup:member"], "dst": ["tag:k3s"],
      "ip": ["tcp:22", "tcp:10250", "tcp:6443", "udp:8472"] },

    // Cluster nodes talk to each other: API, kubelet, VXLAN.
    { "src": ["tag:k3s"], "dst": ["tag:k3s"],
      "ip": ["tcp:6443", "udp:8472", "tcp:10250"] },

    // ...and to the untagged worker (trinity), which is a user device.
    { "src": ["tag:k3s"], "dst": ["autogroup:member"],
      "ip": ["tcp:6443", "udp:8472", "tcp:10250"] }
  ]
}
```

What a volunteer can now reach: the control plane API, kubelet and the VXLAN
peers, and nothing else. No Gitea, no Grafana, no mail, no owner desktop, no
public inbound.

If the existing file already has custom `grants` or `acls` for other services,
keep those entries and add the four above — do not delete a rule you do not
recognise. Only the `"*" → "*:*"` grant must go.

### Test before you save

The ACL editor has a preview/test panel. Check these four:

| From | To | Expect |
| --- | --- | --- |
| `autogroup:member` | `trinity` | allowed |
| `tag:k3s` (or `kituwa-cloud-1`) | `tag:k3s:6443` | allowed |
| `tag:k3s` | `tag:k3s:22` | **denied** |
| `tag:k3s` | Gitea / Grafana host, any port | **denied** |

### Verify after you save (5 minutes)

```bash
# Cluster still healthy: both nodes Ready
ssh kituwa-cloud-1 'sudo -n k3s kubectl get nodes -o wide'

# The host still cannot reach trinity's ntfy directly (the relay is the path)
ssh kituwa-cloud-1 'curl -s -m 4 -o /dev/null -w "%{http_code}\n" http://100.72.19.116:2586/v1/health'
# expect 000

# ntfy still delivers through the relay
ssh kituwa-cloud-1 'sudo -n systemctl is-active kituwa-messages ntfy 2>/dev/null; curl -s http://127.0.0.1:2587/v1/health'
```

If a node goes `NotReady`, the cluster ports are blocked: check that the
`tag:k3s ↔ member` grant is present and that trinity is still untagged.

### Rollback

The ACL editor keeps version history: open the policy editor, choose the previous
version, restore. Every save is also recorded in the tailnet audit log.

## What the minting service does per request

| Step | Value |
| --- | --- |
| Tailscale key | single use, **ephemeral**, pre-authorized, `tag:k3s`, expires in 1 h |
| k3s token | `--usages=agent`, one per node, revocable |
| Node identity | labelled `kituwa.io/volunteer=true` |
| Node taint | `kituwa.io/volunteer=true:NoSchedule` |
| Caps | 25 active volunteer nodes, 3 codes per network per day |

The taint is the other half of the boundary: volunteer nodes run only workloads
that explicitly tolerate it, so nothing that reads secrets is ever scheduled there.
The idle gate (points service) runs on that label and cordons the node the moment
it is busy.

## Revoking a machine

1. **Cluster:** `ssh kituwa-cloud-1 'sudo -n k3s kubectl delete node <name>'`
2. **Tailnet:** <https://login.tailscale.com/admin/machines> → the node → **Remove**.
   Ephemeral keys also remove the device when it goes offline for good.
3. **Code (if it never joined):** `./tools/mint-join-code.sh --revoke <name>` in the
   `k3s-join` repository.

## Turning enrollment off

```bash
ssh kituwa-cloud-1 'sudo -n sed -i "s/^ENROLL_ENABLED=.*/ENROLL_ENABLED=0/" /home/ubuntu/services/kituwa-messages/.env && sudo -n systemctl restart kituwa-messages'
```

With enrollment off the form still works: the request lands in the high-priority
inbox and you answer by hand.

## What is deliberately not automatic

- No device gets `tag:server` or admin rights.
- No volunteer node can schedule into the owner namespace without tolerating the
  taint.
- Mounting the tailnet on a machine does not give it shell access anywhere; the
  ACL above is the whole surface.
