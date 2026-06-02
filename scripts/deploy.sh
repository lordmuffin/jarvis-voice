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

CAPTURE_ENV="${CAPTURE_ENV:-/home/lordmuffin/.jarvis-capture.env}"

if [[ ! -f "${CAPTURE_ENV}" ]]; then
    echo "✗ Required EnvironmentFile missing on $(hostname): ${CAPTURE_ENV}" >&2
    echo "  Generate it with:" >&2
    echo "    python3 -c \"import secrets; print('JARVIS_CAPTURE_KEY='+secrets.token_urlsafe(32))\" > ${CAPTURE_ENV} && chmod 600 ${CAPTURE_ENV}" >&2
    exit 1
fi

echo "→ Deploying to ${REMOTE_ROOT} on $(hostname)"

sudo mkdir -p "${REMOTE_ROOT}"
sudo chown "$(id -u):$(id -g)" "${REMOTE_ROOT}"

rsync -av --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    "${REPO_ROOT}/src/" "${REMOTE_ROOT}/src/"

rsync -av "${REPO_ROOT}/requirements.txt" "${REMOTE_ROOT}/requirements.txt"
rsync -av "${REPO_ROOT}/systemd/" "${REMOTE_ROOT}/systemd/"

venv_install_hint() {
    local id=""
    if [[ -r /etc/os-release ]]; then
        id="$(. /etc/os-release 2>/dev/null && echo "${ID:-}${ID_LIKE:+ ${ID_LIKE}}")"
    fi
    case "${id}" in
        *arch*|*cachyos*) echo "sudo pacman -S python python-pip" ;;
        *debian*|*ubuntu*) echo "sudo apt install python3-venv python3-pip" ;;
        *fedora*|*rhel*|*centos*) echo "sudo dnf install python3-pip" ;;
        *) echo "install your distro's python venv + pip packages" ;;
    esac
}

ensure_ffmpeg() {
    if command -v ffmpeg >/dev/null 2>&1; then
        return 0
    fi
    local id=""
    if [[ -r /etc/os-release ]]; then
        id="$(. /etc/os-release 2>/dev/null && echo "${ID:-}${ID_LIKE:+ ${ID_LIKE}}")"
    fi
    echo "→ Installing ffmpeg (required for server-side Whisper)"
    case "${id}" in
        *arch*|*cachyos*) sudo pacman -S --noconfirm ffmpeg ;;
        *debian*|*ubuntu*) sudo apt update && sudo apt install -y ffmpeg ;;
        *fedora*|*rhel*|*centos*) sudo dnf install -y ffmpeg ;;
        *)
            echo "✗ Unrecognised distro (${id:-unknown}); install ffmpeg manually and re-run." >&2
            exit 1
            ;;
    esac
}

bootstrap_venv() {
    local python_path
    python_path="$(command -v "${PYTHON}" || echo "${PYTHON}")"
    echo "→ Creating venv at ${VENV_DIR} (using ${python_path})"

    rm -rf "${VENV_DIR}"
    if "${PYTHON}" -m venv "${VENV_DIR}" 2>/dev/null; then
        return 0
    fi

    echo "  venv auto-bootstrap failed; retrying with --without-pip"
    rm -rf "${VENV_DIR}"
    "${PYTHON}" -m venv --without-pip "${VENV_DIR}"

    local venv_python="${VENV_DIR}/bin/python"
    if "${venv_python}" -m ensurepip --upgrade --default-pip 2>/dev/null; then
        return 0
    fi

    echo "  ensurepip unavailable; bootstrapping pip via get-pip.py"
    local get_pip
    if command -v curl >/dev/null 2>&1; then
        get_pip=(curl -fsSL https://bootstrap.pypa.io/get-pip.py)
    elif command -v wget >/dev/null 2>&1; then
        get_pip=(wget -qO- https://bootstrap.pypa.io/get-pip.py)
    else
        echo "✗ Neither curl nor wget available to fetch get-pip.py" >&2
        echo "  Interpreter: ${python_path} ($("${PYTHON}" --version 2>&1))" >&2
        echo "  Fix: $(venv_install_hint)" >&2
        return 1
    fi

    if ! "${get_pip[@]}" | "${venv_python}"; then
        echo "✗ Failed to bootstrap pip into ${VENV_DIR}" >&2
        echo "  Interpreter: ${python_path} ($("${PYTHON}" --version 2>&1))" >&2
        echo "  Fix: $(venv_install_hint)" >&2
        return 1
    fi
}

ensure_ffmpeg

if [[ ! -x "${VENV_PIP}" ]]; then
    bootstrap_venv
    "${VENV_PIP}" install --upgrade pip
fi

echo "→ Installing Python deps via ${VENV_PIP}"
"${VENV_PIP}" install -r "${REMOTE_ROOT}/requirements.txt"

echo "→ Installing systemd unit"
sudo install -m 644 "${REMOTE_ROOT}/systemd/jarvis-capture-api.service" /etc/systemd/system/jarvis-capture-api.service
sudo systemctl daemon-reload

echo "→ Enabling and (re)starting jarvis-capture-api"
if sudo systemctl is-enabled jarvis-capture-api >/dev/null 2>&1; then
    sudo systemctl restart jarvis-capture-api
else
    sudo systemctl enable --now jarvis-capture-api
fi

echo "✓ Deploy complete."
