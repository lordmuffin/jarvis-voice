import configparser
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
UNIT = ROOT / "scripts" / "linux-client" / "jarvis-capture.service"
INSTALLER = ROOT / "scripts" / "install-linux-client.sh"


def _unit() -> configparser.ConfigParser:
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str  # type: ignore[assignment,method-assign]
    cp.read(UNIT)
    return cp


def test_unit_runs_the_foreground_daemon_in_standing_endpoint_mode() -> None:
    svc = _unit()["Service"]
    assert (
        svc["ExecStart"] == "%h/.local/bin/jarvis-live capture start --foreground --resume-or-new"
    )
    assert svc["Restart"] == "on-failure"
    assert svc["RestartPreventExitStatus"] == "2"  # config errors must not restart-loop
    assert int(svc["TimeoutStopSec"]) > 30  # stop waits up to 30 s for the server to confirm
    assert _unit()["Install"]["WantedBy"] == "default.target"


def test_unit_has_no_mode_or_credentials_baked_in() -> None:
    text = UNIT.read_text()
    assert "--local-only" not in text and "TOKEN" not in text.upper().replace("TOKEN_FILE", "")


def test_installer_installs_to_the_path_the_unit_executes() -> None:
    assert "uv tool install" in INSTALLER.read_text()  # puts jarvis-live in ~/.local/bin
    assert "jarvis-capture.service" in INSTALLER.read_text()


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_installer_passes_shellcheck() -> None:
    res = subprocess.run(
        ["shellcheck", str(INSTALLER)], capture_output=True, text=True, check=False
    )
    assert res.returncode == 0, res.stdout


def test_installer_is_executable_and_posix_sh() -> None:
    assert INSTALLER.stat().st_mode & 0o111
    assert INSTALLER.read_text().startswith("#!/bin/sh\n")
