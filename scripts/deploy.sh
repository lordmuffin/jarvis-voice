#!/usr/bin/env bash
# Deploy jarvis-voice locally on this host.
#
# Usage:
#   scripts/deploy.sh
#
# Assumes the script is run on andrew-jarvis-v1 (or any host that should
# host the jarvis-voice services). Installs into /opt/jarvis-voice and
# (re)loads systemd units. Requires sudo for /opt and /etc/systemd writes.

set -euo pipefail

REMOTE_ROOT="/opt/jarvis-voice"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-/home/lordmuffin/.whisper-venv}"
VENV_PIP="${VENV_DIR}/bin/pip"
PYTHON="${PYTHON:-python3}"

echo "→ Deploying to ${REMOTE_ROOT} on $(hostname)"

sudo mkdir -p "${REMOTE_ROOT}"
sudo chown "$(id -u):$(id -g)" "${REMOTE_ROOT}"

rsync -av --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    "${REPO_ROOT}/src/" "${REMOTE_ROOT}/src/"

rsync -av "${REPO_ROOT}/requirements.txt" "${REMOTE_ROOT}/requirements.txt"
rsync -av "${REPO_ROOT}/systemd/" "${REMOTE_ROOT}/systemd/"

if [[ ! -x "${VENV_PIP}" ]]; then
    echo "→ Creating venv at ${VENV_DIR} (using ${PYTHON})"
    "${PYTHON}" -m venv "${VENV_DIR}"
    "${VENV_PIP}" install --upgrade pip
fi

echo "→ Installing Python deps via ${VENV_PIP}"
"${VENV_PIP}" install -r "${REMOTE_ROOT}/requirements.txt"

echo "→ Installing systemd units"
sudo install -m 644 "${REMOTE_ROOT}/systemd/jarvis-voice-pipeline.service" /etc/systemd/system/jarvis-voice-pipeline.service
sudo install -m 644 "${REMOTE_ROOT}/systemd/jarvis-capture-api.service" /etc/systemd/system/jarvis-capture-api.service
sudo systemctl daemon-reload

echo "→ Restarting services (if enabled)"
if sudo systemctl is-enabled jarvis-voice-pipeline >/dev/null 2>&1; then
    sudo systemctl restart jarvis-voice-pipeline
else
    echo "  jarvis-voice-pipeline not yet enabled — run: sudo systemctl enable --now jarvis-voice-pipeline"
fi

if sudo systemctl is-enabled jarvis-capture-api >/dev/null 2>&1; then
    sudo systemctl restart jarvis-capture-api
else
    echo "  jarvis-capture-api not yet enabled — run: sudo systemctl enable --now jarvis-capture-api"
fi

echo "✓ Deploy complete."
