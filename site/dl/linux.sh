#!/bin/bash
echo
echo "  Kituwa Cluster — joining this machine as a worker."
echo
CODE="$(curl -fsS -X POST https://k3s.kituwa.com/api/v1/enroll/auto \
  -H 'Content-Type: application/json' --data "{\"node\":\"$(hostname -s)\"}" \
  | sed -n 's/.*"code":"\(k3s1\.[^"]*\)".*/\1/p')"
if [ -z "$CODE" ]; then
  echo "  Could not get a join code. Try https://k3s.kituwa.com/join/ instead."
  exit 1
fi
curl -fsSL https://k3s.kituwa.com/install.sh | sudo bash -s -- --code "$CODE"
