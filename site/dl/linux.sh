#!/bin/bash
# Run me with:  bash kituwa-join.sh      (or right-click → Run in Terminal)
echo
echo "  Kituwa Cluster — this machine becomes a worker node."
echo
read -r -p "Paste your join code (starts with k3s1.): " CODE
if [ -z "$CODE" ]; then
  echo "No code entered. Get one at https://k3s.kituwa.com/join/"
  exit 1
fi
curl -fsSL https://k3s.kituwa.com/install.sh | sudo bash -s -- --code "$CODE"
