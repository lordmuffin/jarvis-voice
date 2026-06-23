#!/usr/bin/env bash
# Deploy the Kai LiveKit voice agent to .155 (DGX Spark).
#
# Usage (run from repo root on GamingPC or locally on .155):
#   scripts/deploy-agent.sh [host]     # default host: lordmuffin@192.168.1.155
#
# Prerequisites on .155:
#   1. SSH key auth configured
#   2. sshfs installed (sudo apt install sshfs)
#   3. /home/lordmuffin/.jarvis-agent.env created (see template below)
#   4. /mnt/notes mount created: sudo mkdir -p /mnt/notes

set -euo pipefail

HOST="${1:-lordmuffin@192.168.1.155}"
REMOTE_ROOT="/opt/jarvis-voice"
VENV="/home/lordmuffin/.agent-venv"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "→ Syncing source to ${HOST}:${REMOTE_ROOT}"
rsync -avz --delete \
    --exclude '__pycache__' --exclude '*.pyc' \
    "${REPO}/src/" "${HOST}:${REMOTE_ROOT}/src/"

rsync -avz "${REPO}/requirements-agent.txt" "${HOST}:${REMOTE_ROOT}/"
rsync -avz "${REPO}/systemd/mnt-notes.mount"          "${HOST}:/tmp/"
rsync -avz "${REPO}/systemd/mnt-notes.automount"      "${HOST}:/tmp/"
rsync -avz "${REPO}/systemd/jarvis-voice-agent.service" "${HOST}:/tmp/"

echo "→ Installing Python deps"
ssh "${HOST}" "
  python3 -m venv ${VENV} 2>/dev/null || true
  ${VENV}/bin/pip install --upgrade pip -q
  ${VENV}/bin/pip install -r ${REMOTE_ROOT}/requirements-agent.txt -q
"

echo "→ Installing systemd units"
ssh "${HOST}" "
  sudo install -m 644 /tmp/mnt-notes.mount          /etc/systemd/system/mnt-notes.mount
  sudo install -m 644 /tmp/mnt-notes.automount      /etc/systemd/system/mnt-notes.automount
  sudo install -m 644 /tmp/jarvis-voice-agent.service /etc/systemd/system/jarvis-voice-agent.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now mnt-notes.automount
  sudo systemctl restart jarvis-voice-agent 2>/dev/null || sudo systemctl enable --now jarvis-voice-agent
"

echo "✓ Deploy complete. Run: ssh ${HOST} journalctl -u jarvis-voice-agent -f"
