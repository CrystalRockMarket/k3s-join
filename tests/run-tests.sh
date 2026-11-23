#!/usr/bin/env bash
# Offline tests for the installers. Nothing here touches the network, the
# cluster, or this machine's configuration.
#
#   tests/run-tests.sh
#
# Exit status is the number of failures.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PASS=0
FAIL=0

ok()   { PASS=$((PASS + 1)); printf '  ok   %s\n' "$*"; }
bad()  { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$*"; }
head_() { printf '\n==> %s\n' "$*"; }

expect() { # expect <description> <command...>
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$desc"; else bad "$desc"; fi
}
expect_fail() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then bad "$desc (expected a non-zero exit)"; else ok "$desc"; fi
}

# ---------------------------------------------------------------- syntax ----
head_ "bash syntax"
for f in install.sh install-macos.sh tools/mint-join-code.sh tools/revoke-node.sh tests/run-tests.sh; do
  expect "$f parses" bash -n "$f"
done

head_ "shellcheck"
if command -v shellcheck >/dev/null 2>&1; then
  for f in install.sh install-macos.sh tools/mint-join-code.sh tools/revoke-node.sh; do
    expect "$f passes shellcheck (errors)" shellcheck -S error -s bash "$f"
  done
else
  printf '  skip shellcheck is not installed\n'
fi

# ------------------------------------------------------------- fixtures ----
head_ "powershell"
if command -v pwsh >/dev/null 2>&1; then
  expect "install.ps1 parses" pwsh -NoProfile -Command \
    '$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("install.ps1",[ref]$null,[ref]$e); if($e){exit 1}'
else
  printf '  skip pwsh is not installed (docker run mcr.microsoft.com/powershell:lts-alpine can check it)\n'
fi
# Two mistakes that cost a whole OS its installer once: a here-string header
# with text after @" and a base64 runner built out of nested double quotes.
if grep -q '@".' install.ps1; then bad "no text after a here-string header"; else ok "no text after a here-string header"; fi
if grep -q 'bash -c \"\$(echo' install.ps1; then bad "no nested-quote base64 runner"; else ok "no nested-quote base64 runner"; fi

head_ "shell portability"
# dash has no pipefail; the checker must hand back the bash command, not crash.
out="$(cat install.sh | sh -s -- --help 2>&1 || true)"
case "$out" in
  *"under bash, not sh"*) ok "piped to sh gives the bash command" ;;
  *) bad "piped to sh gives the bash command" ;;
esac
case "$out" in
  *"Illegal option"*) bad "no raw dash error" ;;
  *) ok "no raw dash error" ;;
esac
expect "join.js uses bash for linux" grep -q "install.sh | sudo bash -s" site/join/join.js
expect "join.js uses bash for macos" grep -q "install-macos.sh | bash -s" site/join/join.js

head_ "join code parsing"
TOKEN="K10abcdefghijklmnopqrstuvwxyz0123456789::server:0123456789abcdef"
TSKEY="tskey-auth-kTESTKEY123-CNTRL"
json="$(printf '{"v":1,"server":"https://100.120.24.106:6443","token":"%s","tailscale_key":"%s","node_name":"test-node"}' "$TOKEN" "$TSKEY")"
CODE="k3s1.$(printf '%s' "$json" | base64 -w0 | tr '+/' '-_' | tr -d '=')"

out="$(bash install.sh --code "$CODE" --check-code 2>&1)" || true
case "$out" in
  *"https://100.120.24.106:6443"*) ok "decodes the server address" ;;
  *) bad "decodes the server address" ;;
esac
case "$out" in
  *"test-node"*) ok "takes the node name from the code" ;;
  *) bad "takes the node name from the code" ;;
esac
case "$out" in
  *"agent usage only"*) ok "reports the token as agent-scoped" ;;
  *) bad "reports the token as agent-scoped" ;;
esac
case "$out" in
  *"$TOKEN"*) bad "must not print the full token" ;;
  *) ok "never prints the full token" ;;
esac
case "$out" in
  *"$TSKEY"*) bad "must not print the tailnet key" ;;
  *) ok "never prints the tailnet key" ;;
esac

head_ "join code rejection"
expect_fail "rejects a code without the prefix" bash install.sh --code "hunter2" --check-code
expect_fail "rejects truncated base64" bash install.sh --code "k3s1.eyJ2Ijox" --check-code
expect_fail "rejects a code with no token" bash install.sh --code "k3s1.$(printf '{"v":1,"server":"https://1.2.3.4:6443"}' | base64 -w0 | tr '+/' '-_' | tr -d '=')" --check-code
expect_fail "rejects a non-https server" bash install.sh --code "k3s1.$(printf '{"v":1,"server":"http://1.2.3.4:6443","token":"%s","tailscale_key":"%s"}' "$TOKEN" "$TSKEY" | base64 -w0 | tr '+/' '-_' | tr -d '=')" --check-code
expect_fail "rejects a mistyped tailnet key" bash install.sh --code "k3s1.$(printf '{"v":1,"server":"https://1.2.3.4:6443","token":"%s","tailscale_key":"nope"}' "$TOKEN" | base64 -w0 | tr '+/' '-_' | tr -d '=')" --check-code

head_ "code file input"
tmp="$(mktemp)"; printf '%s\n' "$CODE" > "$tmp"; chmod 600 "$tmp"
out="$(bash install.sh --code-file "$tmp" --check-code 2>&1)" || true
case "$out" in *"100.120.24.106"*) ok "reads --code-file" ;; *) bad "reads --code-file" ;; esac
rm -f "$tmp"

head_ "operator tool"
out="$(bash tools/mint-join-code.sh --name "Spare PC #2" --dry-run 2>&1)" || true
case "$out" in *"join:spare-pc-2"*) ok "sanitizes the node name" ;; *) bad "sanitizes the node name" ;; esac
case "$out" in *"tskey"*) bad "dry run must not need or print a key" ;; *) ok "dry run needs no tailnet key" ;; esac
expect_fail "mint refuses a name-less run" bash tools/mint-join-code.sh --dry-run
expect_fail "mint rejects an unknown flag" bash tools/mint-join-code.sh --name x --nope

head_ "html"
if [ -f site/index.html ]; then
  expect "page has a title" grep -q "<title>" site/index.html
  expect "page links the repository" grep -q "github.com/CrystalRockMarket/k3s-join" site/index.html
  expect "page installs from the published path" grep -q "https://k3s.kituwa.com/install.sh" site/index.html
  expect "join form exists" test -f site/join/index.html
  expect "join form posts to the intake" grep -q "/api/v1/forms/k3s-join" site/join/join.js
  expect "join form has a honeypot" grep -q '_hp' site/join/index.html
  # Regression: the response was once parsed twice, which threw
  # "res.json is not a function" in the browser.
  counts=$(grep -c "res.json().catch" site/join/join.js || true)
  if [ "$counts" = "1" ]; then ok "join.js parses each response once"; else bad "join.js parses each response once (found $counts)"; fi
  expect "join form asks for a name" grep -q 'for="name"' site/join/index.html
  expect "hero links the join form" grep -q 'href="/join/"' site/index.html
  expect "nginx proxies the intake" grep -q "api/v1/forms/" deploy/k3s-kituwa.conf
  if grep -q "TODO\|FIXME\|lorem ipsum" site/index.html; then bad "no placeholder text"; else ok "no placeholder text"; fi
else
  bad "site/index.html exists"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
exit "$FAIL"
