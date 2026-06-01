#!/usr/bin/env bash
# Deploy jarvis-voice to andrew-jarvis-v1.
#
# Usage:
#   scripts/deploy.sh [host]
#
# Default host: andrew-jarvis-v1
# Override with the first positional arg, e.g.:
#   scripts/deploy.sh lordmuffin@192.168.1.42

set -euo pipefail

HOST="${1:-andrew-jarvis-v1}"
REMOTE_ROOT="/opt/jarvis-voice"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "→ Deploying to ${HOST}:${REMOTE_ROOT}"

ssh "${HOST}" "sudo mkdir -p ${REMOTE_ROOT} && sudo chown \$(id -u):\$(id -g) ${REMOTE_ROOT}"

rsync -av --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    "${REPO_ROOT}/src/" "${HOST}:${REMOTE_ROOT}/src/"

rsync -av "${REPO_ROOT}/requirements.txt" "${HOST}:${REMOTE_ROOT}/requirements.txt"
rsync -av "${REPO_ROOT}/systemd/" "${HOST}:${REMOTE_ROOT}/systemd/"

echo "→ Installing Python deps into ~/.whisper-venv"
ssh "${HOST}" "/home/lordmuffin/.whisper-venv/bin/pip install -r ${REMOTE_ROOT}/requirements.txt"

echo "→ Installing systemd units"
ssh "${HOST}" "sudo install -m 644 ${REMOTE_ROOT}/systemd/jarvis-voice-pipeline.service /etc/systemd/system/jarvis-voice-pipeline.service"
ssh "${HOST}" "sudo install -m 644 ${REMOTE_ROOT}/systemd/jarvis-capture-api.service /etc/systemd/system/jarvis-capture-api.service"
ssh "${HOST}" "sudo systemctl daemon-reload"

echo "→ Restarting services (if enabled)"
ssh "${HOST}" "sudo systemctl is-enabled jarvis-voice-pipeline >/dev/null 2>&1 && sudo systemctl restart jarvis-voice-pipeline || echo '  jarvis-voice-pipeline not yet enabled — run: sudo systemctl enable --now jarvis-voice-pipeline'"
ssh "${HOST}" "sudo systemctl is-enabled jarvis-capture-api >/dev/null 2>&1 && sudo systemctl restart jarvis-capture-api || echo '  jarvis-capture-api not yet enabled — run: sudo systemctl enable --now jarvis-capture-api'"

echo "✓ Deploy complete."
