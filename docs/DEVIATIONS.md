# Deviations from the house onboarding rules

Recorded under protocol 18 (security assurance): what is not applicable here, why,
and what evidence backs that up. Nothing is waived for convenience.

## 1. No sign-in, no consumer auth methods

`k3s.kituwa.com` is a static public page plus three shell/PowerShell scripts. It
has no accounts, no sessions, no database and no user-specific data, so it
publishes no `/.well-known/auth-methods.json` and no login route.

- Rules affected: `auth-methods`, `pocketid`, `login-route`, `login-schema`.
- Why it cannot be satisfied honestly: the contract describes passkey, password,
  MetaMask and Phantom sign-in on a site that has nothing to sign in to.
  Publishing that file would be a false claim.
- What does apply: the operator boundary is the cluster itself. A join code is
  issued by the operator (`tools/mint-join-code.sh`) and revocable; there is no
  web admin surface to protect.
- If an operator console is added later, it gets a Pocket ID `operators` client
  and passkey-only sign-in before it ships.

## 2. Payments: none, by design

The project takes no money: no checkout, no card form, no crypto address. The
manifest records `payments.mode: "none"` with that reason. A future reward
programme would be a product decision, not a payment endpoint on this page.

## 3. Uptime monitor

The `uptime` gate cannot read the toolbox database (`OperationalError: unable to
open database file`) — a defect in the gate environment, not in this site. The
site is covered by the host's nginx and certificate monitoring and by the page's
own `/watchdog` checks.

## 4. Registry mirror to AWS

The directory adapter's second registry copy targets the retired AWS-001 host and
fails with `scp: Connection closed`. The local registry (the source of truth), the
portfolio record, the Start link, the timeline entry and the Atlas node are all
present. Protocol 1 rule 7 and the 2026-11-23 host change mean the second copy
belongs on `kituwa-cloud-1`; that adapter change is tracked separately.
