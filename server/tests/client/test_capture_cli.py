"""`jarvis-live capture ...` end to end, as real subprocesses against the real test server."""

import asyncio
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis_live.replay import ReplayProducer
from tests.audio import TWO_UTTERANCES
from tests.client.conftest import script
from tests.conftest import ServerHandle


class Box:
    """A fake Linux box: private XDG dirs and a fake parec on PATH."""

    def __init__(self, root: Path) -> None:
        self.root = root
        bin_dir = root / "bin"
        bin_dir.mkdir()
        (root / "speech.pcm").write_bytes(TWO_UTTERANCES)
        script(bin_dir / "parec", f"cat {root / 'speech.pcm'}\nexec sleep 120\n")
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "XDG_CONFIG_HOME": str(root / "cfg"),
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_RUNTIME_DIR": str(root / "run"),
            "HOME": str(root),
        }
        for k in ("JARVIS_LIVE_SERVER", "JARVIS_LIVE_TOKEN", "JARVIS_LIVE_MODE"):
            self.env.pop(k, None)
        (root / "run").mkdir(mode=0o700)

    async def jl(self, *args: str, timeout: float = 90) -> subprocess.CompletedProcess[str]:
        return await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "jarvis_live.cli", *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def popen(self, *args: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, "-m", "jarvis_live.cli", *args],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


async def _cleanup(box: Box) -> None:
    await box.jl("capture", "stop")


async def test_configure_writes_a_private_config_and_start_needs_an_explicit_mode(box: Box) -> None:
    res = await box.jl("capture", "start")
    assert res.returncode == 2 and "mode" in res.stderr

    res = await box.jl(
        "capture", "configure", "--server", "http://x", "--token", "t", "--mode", "stream"
    )
    assert res.returncode == 0
    cfg = box.root / "cfg" / "jarvis-live" / "client.toml"
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600

    bad = await box.jl("capture", "configure", "--mode", "auto")
    assert bad.returncode == 2


async def test_not_running_is_exit_3(box: Box) -> None:
    assert (await box.jl("capture", "status")).returncode == 3
    res = await box.jl("capture", "stop")
    assert res.returncode == 3 and "not running" in res.stdout + res.stderr


async def test_one_command_starts_a_streaming_session_visible_in_the_dashboard_api(
    box: Box, server: ServerHandle, token: str
) -> None:
    cfg = await box.jl(
        "capture", "configure", "--server", server.url, "--token", token, "--mode", "stream"
    )
    assert cfg.returncode == 0, cfg.stderr
    try:
        start = await box.jl("capture", "start", "--title", "SSH test", timeout=60)
        assert start.returncode == 0, start.stderr
        assert "STREAMING" in start.stdout and f"{server.url}/sessions/" in start.stdout
        sid = start.stdout.split("/sessions/")[1].split()[0]

        again = await box.jl("capture", "start")
        assert again.returncode == 1 and "already running" in again.stderr

        status = await box.jl("capture", "status")
        assert status.returncode == 0 and "STREAMING" in status.stdout and sid in status.stdout

        tail = box.popen("capture", "tail")
        await asyncio.sleep(1.5)
        stop = await box.jl("capture", "stop", timeout=90)
        assert stop.returncode == 0, stop.stderr
        out, _ = await asyncio.to_thread(tail.communicate, None, 30)
        assert "seg-" in out  # FakeSTT's text, relayed live through the daemon

        # The same transcript is available to a second terminal, from the server's side.
        later = await box.jl("capture", "tail", "--session", sid, "--no-follow")
        assert later.returncode == 0 and "seg-" in later.stdout
    finally:
        await _cleanup(box)


async def test_local_only_start_status_tail_stop(box: Box) -> None:
    res = await box.jl("capture", "start", "--local-only", timeout=60)
    try:
        assert res.returncode == 0, res.stderr
        assert "LOCAL ONLY" in res.stdout and "nothing leaves this machine" in res.stdout
        assert "/sessions/" not in res.stdout
        st = await box.jl("capture", "status")
        assert "LOCAL ONLY" in st.stdout
        tail = await box.jl("capture", "tail")
        assert tail.returncode == 0 and "local-only: no transcript" in tail.stdout
    finally:
        stop = await box.jl("capture", "stop")
    assert stop.returncode == 0


async def test_start_without_a_recorder_says_what_to_install(box: Box) -> None:
    (box.root / "bin" / "parec").unlink()
    env_path = str(box.root / "bin")
    box.env["PATH"] = env_path  # nothing else on PATH: python is invoked by absolute path
    res = await box.jl("capture", "start", "--local-only")
    assert res.returncode == 1 and "pulseaudio-utils" in res.stderr


async def test_status_json_is_machine_readable(box: Box) -> None:
    import json

    await box.jl("capture", "start", "--local-only", timeout=60)
    try:
        st = json.loads((await box.jl("capture", "status", "--json")).stdout)
        assert st["mode"] == "LOCAL ONLY" and st["session"]
    finally:
        await _cleanup(box)


async def test_viewer_tail_shows_a_session_recorded_elsewhere(
    box: Box, server: ServerHandle, token: str
) -> None:
    await box.jl(
        "capture", "configure", "--server", server.url, "--token", token, "--mode", "stream"
    )
    rp = ReplayProducer(server.url, token, speed=50.0)
    res = await rp.run(TWO_UTTERANCES)
    out = await box.jl("capture", "tail", "--session", res.session_id, "--no-follow")
    assert out.returncode == 0 and "seg-" in out.stdout


async def test_configure_does_not_persist_environment_overrides(box: Box) -> None:
    box.env["JARVIS_LIVE_TOKEN"] = "from-the-environment"
    res = await box.jl("capture", "configure", "--server", "http://x", "--mode", "stream")
    assert res.returncode == 0
    saved = (box.root / "cfg" / "jarvis-live" / "client.toml").read_text()
    assert "from-the-environment" not in saved
