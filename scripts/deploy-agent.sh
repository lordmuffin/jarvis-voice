#!/usr/bin/env bash
# Deploy the Kai LiveKit voice agent locally on .155 (DGX Spark).
#
# Run this script directly on .155 from the repo root:
#   scripts/deploy-agent.sh
#
# Prerequisites:
#   1. sshfs installed (sudo apt install sshfs)
#   2. /home/lordmuffin/.jarvis-agent.env created (see template below)
#   3. /mnt/notes mount created: sudo mkdir -p /mnt/notes

set -euo pipefail

REMOTE_ROOT="/opt/jarvis-voice"
VENV="/home/lordmuffin/.agent-venv"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "→ Syncing source to ${REMOTE_ROOT}"
sudo mkdir -p "${REMOTE_ROOT}/src"
sudo chown -R "$(id -u):$(id -g)" "${REMOTE_ROOT}"
rsync -avz --delete \
    --exclude '__pycache__' --exclude '*.pyc' \
    "${REPO}/src/" "${REMOTE_ROOT}/src/"

cp "${REPO}/requirements-agent.txt" "${REMOTE_ROOT}/"
cp "${REPO}/systemd/mnt-notes.mount"              /tmp/
cp "${REPO}/systemd/mnt-notes.automount"          /tmp/
cp "${REPO}/systemd/jarvis-voice-agent.service"   /tmp/
cp "${REPO}/systemd/jarvis-capture-api.service"   /tmp/

echo "→ Installing Python deps (shared venv for agent + capture API)"
python3 -m venv "${VENV}" 2>/dev/null || true
"${VENV}/bin/pip" install --upgrade pip -q
"${VENV}/bin/pip" install -r "${REMOTE_ROOT}/requirements-agent.txt" -q

echo "→ Installing systemd units"
sudo install -m 644 /tmp/mnt-notes.mount             /etc/systemd/system/mnt-notes.mount
sudo install -m 644 /tmp/mnt-notes.automount         /etc/systemd/system/mnt-notes.automount
sudo install -m 644 /tmp/jarvis-voice-agent.service  /etc/systemd/system/jarvis-voice-agent.service
sudo install -m 644 /tmp/jarvis-capture-api.service  /etc/systemd/system/jarvis-capture-api.service

sudo systemctl daemon-reload
sudo systemctl enable --now mnt-notes.automount
sudo systemctl restart jarvis-voice-agent   2>/dev/null || sudo systemctl enable --now jarvis-voice-agent
sudo systemctl restart jarvis-capture-api   2>/dev/null || sudo systemctl enable --now jarvis-capture-api

echo "✓ Deploy complete."
echo "  Logs: journalctl -u jarvis-voice-agent -f"
echo "  Logs: journalctl -u jarvis-capture-api -f"
