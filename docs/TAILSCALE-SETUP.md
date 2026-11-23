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

The tag is what keeps a volunteer machine contained. Paste this into
<https://login.tailscale.com/admin/acls> (merge with your existing policy, do not
replace it):

```jsonc
{
  "tagOwners": {
    "tag:k3s": ["autogroup:admin"]
  },
  "grants": [
    // A cluster node may reach the control plane API and its peers' VXLAN.
    { "src": ["tag:k3s"], "dst": ["tag:k3s"], "ip": ["tcp:6443", "udp:8472", "tcp:10250"] },
    // The admin (you) may reach a node's kubelet for diagnostics.
    { "src": ["autogroup:admin"], "dst": ["tag:k3s"], "ip": ["tcp:22", "tcp:10250"] }
  ]
}
```

What this buys you:

- a volunteer node can talk to the cluster on **6443, 8472, 10250** and nothing else;
- it cannot reach Trilian, Gitea, Grafana, the mail server, or your desktop;
- it cannot be reached from the public internet at all;
- a node that leaves the tailnet stops being reachable immediately.

If your current policy uses `acls` instead of `grants`, write the same intent with
`"action": "accept"` rules: `tag:k3s → tag:k3s:6443,8472,10250`.

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
