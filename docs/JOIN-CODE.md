# Join codes

A join code is one line, safe to paste into a terminal, never safe to paste into a
chat room.

```
k3s1.eyJ2IjoxLCJzZXJ2ZXIiOiJodHRwczovLzEwMC4xMjAuMjQuMTA2OjY0NDMiLCJ0b2tlbiI6...
```

## Format

`k3s1.` + base64url (no padding) of one JSON object:

```json
{
  "v": 1,
  "server": "https://100.120.24.106:6443",
  "token": "<bootstrap token>",
  "tailscale_key": "tskey-auth-…",
  "node_name": "spare-pc"
}
```

`v` exists so a future format can be rejected by an old installer instead of
being misread.

## What each part grants

| Field | Grants | Does not grant |
| --- | --- | --- |
| `server` | the address the agent connects to | any remote access to the machine |
| `token` | joining one worker (`--usages=agent`) | reading secrets, running commands, scheduling |
| `tailscale_key` | a device on the tailnet with `tag:k3s` | access to other tailnet devices beyond the ACL |
| `node_name` | a suggested name only | anything; `--node-name` overrides it |

## Lifetime and revocation

- The bootstrap token is minted with `--ttl 0` by default. That is not laziness:
  a k3s agent reads its token at every start, so a token that expires turns into a
  node that cannot come back after a reboot. Access is controlled by revocation,
  not by a clock.
- Revoke a code with `./tools/mint-join-code.sh --revoke <name>`. It deletes the
  bootstrap secret on the control plane. New joins with that code fail; machines
  already joined keep running on their own certificates.
- To remove a machine that already joined, delete the Node object
  (`./tools/revoke-node.sh <name>`); its pods drain to other workers. Then remove
  the device from the tailnet, or let the volunteer run `--uninstall
  --remove-tailscale`.

## Handling

- One code per machine. Never one code shared among several.
- Hand it over directly — a terminal paste, a password manager, not a public
  channel or an issue.
- If a code leaks: revoke it, then mint a new one. Nothing else is affected.
- The code contains no cluster CA, no kubeconfig and no client certificate.
  Someone who reads it can add a worker; they cannot administer the cluster.
