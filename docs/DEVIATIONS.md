# Deviations from the house onboarding rules

Recorded under protocol 18 (security assurance): what is not applicable here, why,
and what evidence backs that up. Nothing is waived for convenience.

## 1. Consumer auth is narrower than the four-method default

The site now has consumer accounts for the Earn programme. The published contract
offers **passkey first, Pocket ID second, password third**, with email
verification. MetaMask and Phantom are not offered, and the contract does not
claim them.

- Evidence: `/.well-known/auth-methods.json`, the sign-in page, and a completed
  password reset through the site's own mail.
- Operator access is Pocket ID only. The client is named
  `K3s Cluster Earn — Operator`, scoped to `operators`, with callback
  `https://k3s.kituwa.com/admin/oidc/callback`. The consumer client is
  `K3s Cluster Earn — Member`, scoped to `members`.
- The onboarding tool cannot read the Pocket ID client because the hosted
  `tools.kituwa.com` service does not have `pocket-id-admin` installed. The
  client, group, callback, env presence and redirect were verified manually.
  Launch gate `pocketid` therefore stays `pending`, not failed.

## 2. Payments: none, by design

The project takes no money. Earn points are a loyalty credit with no cash value,
not a token, security or investment. There is no checkout, card form or crypto
address. The manifest records `payments.mode: "none"`.

Premium is a reward issued as a digital token. It is not sold, and the API that
verifies it is free.

## 3. The API is free — no allowance, no tiers

`/api/` and `/api/docs/` publish a read-only, keyed HTTPS API and the matching
MCP server. It is free: there is no monthly allowance, no call counter and no
paid tier. Keys are used only to attribute and revoke access. A per-minute rate
limit protects the service. The launch gate's `api-quota` row stays `pending`
because that gate expects a paid-tier policy; the product decision is the
opposite, so there is nothing to publish.

## 4. Uptime monitor

The `uptime` gate cannot read the toolbox database (`OperationalError: unable to
open database file`) — a defect in the gate environment, not in this site. The
site is covered by the host's nginx and certificate monitoring and by the page's
own `/watchdog` checks.

## 5. Registry mirror and backup visibility

The directory adapter's second registry copy now targets the primary host
(`kituwa-cloud-1`), not the retired AWS-001. The record was mirrored manually
after the adapter's SSH wrapper failed on a read-only gcloud config directory.
The local registry, portfolio record, Start link, Hub entry, timeline, Atlas node
and remote copy are present.

The launch gate's `backup` row cannot see the trinity source path
(`/home/airdrop/projects/k3s-join`) because the gate runs on the host. The source
of truth is on the worker; the backup run is `gitea-backup k3s-join`.
