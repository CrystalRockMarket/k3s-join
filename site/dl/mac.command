#!/bin/bash
VERSION="1.3.0"
echo "  Kituwa Cluster installer v$VERSION"
echo
echo "  Kituwa Cluster — joining this Mac as a worker."
echo
CODE="$(curl -fsS -X POST https://k3s.kituwa.com/api/v1/enroll/auto \
  -H 'Content-Type: application/json' --data "{\"node\":\"$(scutil --get ComputerName 2>/dev/null | tr -cd '[:alnum:]-¬' | cut -c1-40)mac\"}" \
  | sed -n 's/.*"code":"\(k3s1\.[^"]*\)".*/\1/p')"
if [ -z "$CODE" ]; then
  echo "  Could not get a join code. Try https://k3s.kituwa.com/join/ instead."
  read -r -p "Press return to close. " _
  exit 1
fi
curl -fsSL https://k3s.kituwa.com/install-macos-1.3.0.sh | bash -s -- --code "$CODE"
echo
echo "Finished. You can close this window; the worker keeps running."
read -r -p "Press return to close. " _
