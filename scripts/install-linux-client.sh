#!/bin/sh
# Install the Jarvis Live headless capture client for the current user.
#
#   curl -fsSL https://raw.githubusercontent.com/lordmuffin/jarvis-voice/main/scripts/install-linux-client.sh | sh
#
# Environment:
#   JARVIS_LIVE_REF   git ref to install (default: main; use a server-vX.Y.Z tag to pin)
#   JARVIS_LIVE_REPO  repository URL (default: the public GitHub repository)
set -eu

REPO="${JARVIS_LIVE_REPO:-https://github.com/lordmuffin/jarvis-voice}"
REF="${JARVIS_LIVE_REF:-main}"
UNIT_URL="https://raw.githubusercontent.com/lordmuffin/jarvis-voice/${REF}/scripts/linux-client/jarvis-capture.service"

say() { printf '%s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

if [ "$(uname -s)" != "Linux" ]; then
    say "This installer is for Linux. On macOS use the Jarvis Live app." >&2
    exit 1
fi

if have uv; then
    :
elif have pipx; then
    INSTALLER=pipx
else
    say "Installing uv (https://docs.astral.sh/uv/)..."
    curl -fsSL https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
fi

SPEC="jarvis-live @ git+${REPO}@${REF}#subdirectory=server"
if [ "${INSTALLER:-uv}" = pipx ]; then
    pipx install --force "$SPEC"
else
    uv tool install --force --python 3.12 "$SPEC"
fi

# The client records with the sound server's own tool; it needs no audio library.
if ! have parec && ! have pw-record; then
    say ""
    say "No audio recorder found. Install one:"
    say "  Debian/Ubuntu: sudo apt install pulseaudio-utils"
    say "  Fedora:        sudo dnf install pulseaudio-utils"
    say "  Arch:          sudo pacman -S libpulse"
fi

if have systemctl; then
    UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$UNIT_DIR"
    curl -fsSL "$UNIT_URL" -o "$UNIT_DIR/jarvis-capture.service"
    systemctl --user daemon-reload 2>/dev/null || true
    say ""
    say "Installed the systemd user unit (not enabled). To make this box a standing endpoint:"
    say "  loginctl enable-linger \"\$USER\"           # keep it running after you log out"
    say "  systemctl --user enable --now jarvis-capture"
fi

say ""
say "Installed. Next:"
say "  jarvis-live capture configure --server https://YOUR-SERVER --token YOUR-DEVICE-TOKEN --mode stream"
say "  jarvis-live capture start"
say "Create the token on the server with: jarvis-live create-device --name \"\$(hostname)\""
