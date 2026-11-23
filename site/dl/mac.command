#!/bin/bash
# Double-click this file. Terminal opens and asks for your join code.
echo
echo "  Kituwa Cluster — this Mac joins as a worker inside a small Linux VM."
echo
read -r -p "Paste your join code (starts with k3s1.): " CODE
if [ -z "$CODE" ]; then
  echo "No code entered. Get one at https://k3s.kituwa.com/join/"
  read -r -p "Press return to close. " _
  exit 1
fi
curl -fsSL https://k3s.kituwa.com/install-macos.sh | bash -s -- --code "$CODE"
echo
echo "Finished. You can close this window; the worker keeps running."
read -r -p "Press return to close. " _
