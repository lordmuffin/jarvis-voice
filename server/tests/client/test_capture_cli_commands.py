"""`jarvis-live capture ...` called in this process against a real control socket.

test_capture_cli.py proves the commands end to end through subprocesses. This file covers what
those runs cannot reach: what each command prints and returns for every daemon answer, including
the unhappy ones a healthy daemon never gives. The daemon is a real ``ControlServer`` with a
scripted handler; everything from the command line to the socket is the real code."""

import argparse
import asyncio
import json
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from jarvis_live import cli
from jarvis_live.client import capture_cli, control
from jarvis_live.client.config import ClientConfig, load_config, runtime_dir
from jarvis_live.client.control import ControlServer
from jarvis_live.copilot.schema import FinalNote
from jarvis_live.replay import ReplayProducer
from tests.audio import TWO_UTTERANCES
from tests.client.conftest import script
from tests.conftest import ServerHandle
from tests.fakes import FakeLLM

STATUS = {
    "mode": "STREAMING",
    "mode_note": "audio goes to the server",
    "session": "abc",
    "title": "Standup",
    "state": "recording",
    "connection": "connected",
    "problem": "uplink is slow",
    "uptime_s": 3661,
    "unacked_ms": 1500,
    "dashboard_url": "https://j.example/sessions/abc",
    "device": "laptop",
    "source": None,
}
SEGMENT = {"type": "segment", "id": "s1", "channel": "mic", "speaker": "me", "start_ms": 61_000}
SEGMENT |= {"end_ms": 62_000, "text": "hello there", "stt_tier": "gpu"}


class ScriptedDaemon:
    """A real control socket on its own event loop, answering from ``replies``."""

    def __init__(self, env: dict[str, str]) -> None:
        self.replies: dict[str, Any] = {"status": STATUS}
        self.events: list[dict[str, Any]] = []
        self.seen: list[str] = []
        self._path = runtime_dir(env) / "ctl.sock"
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._server = ControlServer(self._path, self._handle)

    async def _handle(self, msg: dict[str, Any], send: control.Send) -> None:
        cmd = msg["cmd"]
        self.seen.append(cmd)
        if cmd == "subscribe":
            for event in self.events:
                await send(event)
            return
        reply = self.replies[cmd]
        if isinstance(reply, Exception):
            raise reply
        await send(reply)

    def __enter__(self) -> "ScriptedDaemon":
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._server.start(), self._loop).result(5)
        return self

    def __exit__(self, *exc: object) -> None:
        asyncio.run_coroutine_threadsafe(self._server.close(), self._loop).result(5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)
        self._loop.close()


@pytest.fixture
def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> dict[str, str]:
    """A private XDG home with a fake parec first on PATH, in this process's environment."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "speech.pcm").write_bytes(TWO_UTTERANCES)
    script(bin_dir / "parec", f"cat {tmp_path / 'speech.pcm'}\nexec sleep 120\n")
    # Short enough for a unix socket path, so the shared /tmp fallback is never used.
    run = Path(tempfile.mkdtemp(prefix="jl-", dir="/tmp"))
    request.addfinalizer(lambda: shutil.rmtree(run, ignore_errors=True))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("JARVIS_LIVE_SERVER", "JARVIS_LIVE_TOKEN", "JARVIS_LIVE_MODE"):
        monkeypatch.delenv(name, raising=False)
    assert len(str(runtime_dir(os.environ) / "ctl.sock")) < 100
    runtime_dir(os.environ).mkdir(mode=0o700)
    return dict(os.environ)


@pytest.fixture
def daemon(env: dict[str, str]) -> Iterator[ScriptedDaemon]:
    with ScriptedDaemon(env) as d:
        yield d


def jl(*args: str) -> int:
    return cli.main(["capture", *args])


# --- configure -----------------------------------------------------------------------------


def test_configure_refuses_an_unknown_mode(
    env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert jl("configure", "--mode", "auto") == capture_cli.EXIT_USAGE
    assert "mode must be one of" in capsys.readouterr().err


def test_configure_reads_the_token_from_a_file_so_it_stays_out_of_the_process_list(
    env: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = tmp_path / "token"
    secret.write_text("from-a-file\n")
    assert jl("configure", "--token-file", str(secret), "--token", "ignored") == 0
    assert load_config(env).token == "from-a-file"
    assert "not set" in capsys.readouterr().out  # no mode was chosen yet


def test_configure_saves_every_field_it_is_given(env: dict[str, str]) -> None:
    args = ["--server", "http://s", "--token", "t", "--mode", "stream"]
    assert jl("configure", *args, "--device", "laptop", "--source", "mic.2") == 0
    cfg = load_config(env)
    assert (cfg.server, cfg.token, cfg.mode) == ("http://s", "t", "stream")
    assert (cfg.device, cfg.source) == ("laptop", "mic.2")


# --- start ---------------------------------------------------------------------------------


def _child(tmp_path: Path, body: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the python that `start` launches, to control how the daemon child behaves."""
    monkeypatch.setattr(sys, "executable", str(script(tmp_path / "child", body)))


def test_start_says_so_when_a_daemon_is_already_running(
    daemon: ScriptedDaemon, capsys: pytest.CaptureFixture[str]
) -> None:
    assert jl("start", "--local-only") == capture_cli.EXIT_FAIL
    assert "already running" in capsys.readouterr().err


def test_start_shows_the_log_when_the_daemon_dies_at_once(
    env: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _child(tmp_path, "echo 'bad python' >&2\nexit 7\n", monkeypatch)
    assert jl("start", "--local-only") == capture_cli.EXIT_FAIL
    err = capsys.readouterr().err
    assert "exit 7" in err and "bad python" in err


def test_start_gives_up_after_the_timeout_and_points_at_the_log(
    env: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _child(tmp_path, "exec sleep 2\n", monkeypatch)
    monkeypatch.setattr(capture_cli, "START_TIMEOUT_S", 0.3)
    assert jl("start", "--local-only") == capture_cli.EXIT_FAIL
    assert "timed out starting" in capsys.readouterr().err


def test_start_passes_every_option_on_to_the_daemon(
    env: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = tmp_path / "argv"
    _child(tmp_path, f'echo "$@" > {argv}\nexit 1\n', monkeypatch)
    jl(
        "start",
        "--local-only",
        "--meeting",
        "--system",
        "--resume",
        "--resume-or-new",
        "--title",
        "Plan",
        "--source",
        "usb",
    )
    assert argv.read_text().split() == [
        "-m",
        "jarvis_live.cli",
        "capture",
        "start",
        "--foreground",
        "--local-only",
        "--meeting",
        "--system",
        "--resume",
        "--resume-or-new",
        "--title",
        "Plan",
        "--source",
        "usb",
    ]


def test_start_stops_a_daemon_that_reports_it_cannot_record(
    env: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The first status finds no daemon; once started, it says it failed and must not linger."""
    _child(tmp_path, "exec sleep 5\n", monkeypatch)
    answers = iter([None, {"state": "failed", "problem": "no such device"}, {"ended": True}])
    asked: list[str] = []

    def ask(_env: Any, cmd: str, timeout: float = 10.0) -> dict[str, Any] | None:
        asked.append(cmd)
        return next(answers)

    monkeypatch.setattr(capture_cli, "_ask", ask)
    assert jl("start", "--local-only") == capture_cli.EXIT_FAIL
    assert "failed: no such device" in capsys.readouterr().err
    assert asked == ["status", "status", "stop"]


def test_start_reports_a_session_with_its_dashboard_link(
    env: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _child(tmp_path, "exec sleep 5\n", monkeypatch)
    answers: Iterator[dict[str, Any] | None] = iter([None, STATUS])
    monkeypatch.setattr(capture_cli, "_ask", lambda *a, **k: next(answers))
    assert jl("start", "--local-only") == capture_cli.EXIT_OK
    out = capsys.readouterr().out
    assert "STREAMING (audio goes to the server)" in out
    assert "session   abc" in out and "dashboard https://j.example/sessions/abc" in out


def test_foreground_runs_the_real_daemon_until_sigterm(
    env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """The systemd path: the daemon runs in this process and stops cleanly on the signal."""
    sock = runtime_dir(env) / "ctl.sock"

    def stop_when_recording() -> None:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if sock.exists():
                os.kill(os.getpid(), signal.SIGTERM)
                return
            time.sleep(0.05)

    stopper = threading.Thread(target=stop_when_recording, daemon=True)
    stopper.start()
    assert jl("start", "--foreground", "--local-only", "--title", "Standing") == 0
    stopper.join(5)
    assert not sock.exists()  # the socket is removed on the way out


def test_a_daemon_that_cannot_run_is_reported_not_raised(
    env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def refuse(self: Any) -> int:
        raise RuntimeError("another capture daemon is running")

    monkeypatch.setattr("jarvis_live.client.daemon.Daemon.run", refuse)
    assert jl("start", "--foreground", "--local-only") == capture_cli.EXIT_FAIL
    assert "another capture daemon" in capsys.readouterr().err


def test_ctrl_c_exits_130(env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(*_: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(capture_cli, "_ask", interrupted)
    assert jl("status") == 130


# --- stop ----------------------------------------------------------------------------------


def test_stop_prints_the_final_note_of_a_session_the_server_confirmed(
    daemon: ScriptedDaemon, capsys: pytest.CaptureFixture[str]
) -> None:
    note = {"title": "Standup notes", "path": "Inbox/standup.md"}
    daemon.replies["stop"] = {"ended": True, "session": "abc", "final_note": note}
    assert jl("stop") == capture_cli.EXIT_OK
    out = capsys.readouterr().out
    assert "session abc ended" in out and "final note: Standup notes (Inbox/standup.md)" in out


def test_stop_without_a_note_still_counts_as_ended(
    daemon: ScriptedDaemon, capsys: pytest.CaptureFixture[str]
) -> None:
    daemon.replies["stop"] = {"ended": True, "session": "abc"}
    assert jl("stop") == capture_cli.EXIT_OK
    assert "final note" not in capsys.readouterr().out


def test_stop_unconfirmed_by_the_server_fails_and_says_the_recording_is_safe(
    daemon: ScriptedDaemon, capsys: pytest.CaptureFixture[str]
) -> None:
    daemon.replies["stop"] = {"ended": False, "session": "abc"}
    assert jl("stop") == capture_cli.EXIT_FAIL
    err = capsys.readouterr().err
    assert "not confirmed" in err and "--resume" in err


def test_stop_reports_a_daemon_error(
    daemon: ScriptedDaemon, capsys: pytest.CaptureFixture[str]
) -> None:
    daemon.replies["stop"] = RuntimeError("the disk is full")
    assert jl("stop") == capture_cli.EXIT_FAIL
    assert "the disk is full" in capsys.readouterr().err


# --- status --------------------------------------------------------------------------------


def test_status_when_nothing_runs(env: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    assert jl("status") == capture_cli.EXIT_NOT_RUNNING
    assert capsys.readouterr().out.strip() == "not running"
    assert jl("status", "--json") == capture_cli.EXIT_NOT_RUNNING
    assert json.loads(capsys.readouterr().out) == {"state": "not running"}


def test_status_prints_every_field_a_person_needs(
    daemon: ScriptedDaemon, capsys: pytest.CaptureFixture[str]
) -> None:
    assert jl("status") == capture_cli.EXIT_OK
    out = capsys.readouterr().out
    assert 'session    abc  "Standup"' in out
    assert "state      recording (connected)" in out
    assert "problem    uplink is slow" in out
    assert "uptime     01:01:01" in out
    assert "unsent     1.5 s of audio" in out
    assert "dashboard  https://j.example/sessions/abc" in out
    assert "device     laptop   source default" in out


def test_status_leaves_out_what_does_not_apply(
    daemon: ScriptedDaemon, capsys: pytest.CaptureFixture[str]
) -> None:
    daemon.replies["status"] = {
        "mode": "LOCAL ONLY",
        "session": "abc",
        "state": "recording",
        "device": "laptop",
        "source": "usb",
    }
    assert jl("status") == capture_cli.EXIT_OK
    out = capsys.readouterr().out
    assert "problem" not in out and "dashboard" not in out and '"' not in out
    assert out.startswith("LOCAL ONLY\n") and "source usb" in out


def test_status_json_is_the_daemon_reply_verbatim(
    daemon: ScriptedDaemon, capsys: pytest.CaptureFixture[str]
) -> None:
    assert jl("status", "--json") == capture_cli.EXIT_OK
    assert json.loads(capsys.readouterr().out) == STATUS


def test_a_daemon_that_answers_with_an_error_counts_as_not_running(
    daemon: ScriptedDaemon, capsys: pytest.CaptureFixture[str]
) -> None:
    daemon.replies["status"] = RuntimeError("still starting")
    assert jl("status") == capture_cli.EXIT_NOT_RUNNING


# --- tail ----------------------------------------------------------------------------------


def test_tail_needs_a_daemon(env: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    assert jl("tail") == capture_cli.EXIT_NOT_RUNNING
    assert "not running" in capsys.readouterr().err


def test_tail_of_a_local_session_explains_why_there_is_no_transcript(
    daemon: ScriptedDaemon, capsys: pytest.CaptureFixture[str]
) -> None:
    daemon.replies["status"] = {**STATUS, "mode": "LOCAL ONLY"}
    assert jl("tail") == capture_cli.EXIT_OK
    assert "no transcript" in capsys.readouterr().out
    assert "subscribe" not in daemon.seen  # never asks for what cannot exist


@pytest.mark.parametrize("follow", [(), ("--no-follow",)])
def test_tail_prints_the_daemon_backlog_and_skips_what_it_cannot_parse(
    daemon: ScriptedDaemon, capsys: pytest.CaptureFixture[str], follow: tuple[str, ...]
) -> None:
    daemon.events = [{"type": "from-the-future"}, SEGMENT]
    assert jl("tail", *follow) == capture_cli.EXIT_OK
    assert capsys.readouterr().out.strip() == "[01:01] me: hello there"


def test_tail_reports_a_socket_that_stops_working(
    daemon: ScriptedDaemon,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def broken(path: Path) -> Any:
        raise OSError("connection reset")
        yield

    monkeypatch.setattr(control, "subscribe", broken)
    assert jl("tail") == capture_cli.EXIT_FAIL
    assert "connection reset" in capsys.readouterr().err


def test_tail_of_a_session_needs_a_server_and_token(
    env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert jl("tail", "--session", "abc") == capture_cli.EXIT_USAGE
    assert "capture configure" in capsys.readouterr().err


@pytest.fixture
def noting_server(make_server: Callable[..., ServerHandle], tmp_path: Path) -> ServerHandle:
    """A server that writes the note, so its sessions reach ``done``."""
    llm = FakeLLM().queue("FinalNote", FinalNote(title="Standup", summary="Short one."))
    return make_server(llm=llm, outbox_dir=tmp_path / "outbox", finalize_recover_max_age_s=0)


async def _configured(env: dict[str, str], server: ServerHandle, token: str) -> None:
    args = ["--server", server.url, "--token", token, "--mode", "stream"]
    assert await asyncio.to_thread(jl, "configure", *args) == 0


async def _until_done(server: ServerHandle, token: str, sid: str) -> None:
    async with httpx.AsyncClient(
        base_url=server.url, headers={"Authorization": f"Bearer {token}"}
    ) as c:
        for _ in range(400):
            status = (await c.get(f"/v1/sessions/{sid}")).json()["status"]
            if status == "done":
                return
            await asyncio.sleep(0.05)
    raise AssertionError(f"the session never finished: {status}")


async def test_tail_of_a_finished_session_prints_it_and_returns(
    env: dict[str, str],
    noting_server: ServerHandle,
    token: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _configured(env, noting_server, token)
    producer = ReplayProducer(noting_server.url, token, speed=50.0)
    sid = (await producer.run(TWO_UTTERANCES)).session_id
    await _until_done(noting_server, token, sid)
    capsys.readouterr()
    # No --no-follow: a done session has nothing left to follow, so tail returns by itself.
    assert await asyncio.to_thread(jl, "tail", "--session", sid) == capture_cli.EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert lines and all("seg-" in x for x in lines)


async def test_tail_of_a_live_session_prints_stored_segments_once_then_follows(
    env: dict[str, str],
    noting_server: ServerHandle,
    token: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A viewer that joins mid-session gets the stored segments, then the live ones, and the
    live feed never repeats one it already printed."""
    producer = ReplayProducer(noting_server.url, token, speed=50.0)
    sid = await producer.create_session()
    half = len(TWO_UTTERANCES) // 2 // 2 * 2
    first = await producer.run(TWO_UTTERANCES[:half], session_id=sid, end=False)
    args = argparse.Namespace(session=sid, no_follow=False)
    cfg = ClientConfig(server=noting_server.url, token=token, mode="stream")
    capsys.readouterr()
    # The server keeps a viewer open after the session ends (like `tail -f`): cancel it.
    tail = asyncio.create_task(capture_cli._tail_viewer(args, cfg))
    await producer.run(TWO_UTTERANCES, session_id=sid, resume_from=first.acked["mic"])
    await _until_done(noting_server, token, sid)

    out: list[str] = []

    async def until_the_final_note() -> None:
        while not any(x.startswith("final note: Standup") for x in out):
            out.extend(capsys.readouterr().out.splitlines())
            await asyncio.sleep(0.05)

    await asyncio.wait_for(until_the_final_note(), 15)  # the note arrives over the live feed
    tail.cancel()
    await asyncio.gather(tail, return_exceptions=True)
    segments = [x for x in out if "seg-" in x]
    assert len(segments) >= 2 and len(segments) == len(set(segments))
