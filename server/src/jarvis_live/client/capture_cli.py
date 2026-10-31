"""``jarvis-live capture ...``: start, stop, status and tail a headless capture session."""

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jarvis_live.client import capture, control
from jarvis_live.client.config import (
    MODES,
    ClientConfig,
    ConfigError,
    config_dir,
    load_config,
    runtime_dir,
    save_config,
    state_dir,
)
from jarvis_live.client.render import TailRenderer
from jarvis_live.protocol import Segment, ServerMessage

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_NOT_RUNNING = 0, 1, 2, 3
START_TIMEOUT_S = 25.0


def add_parser(sub: Any) -> None:
    cap = sub.add_parser("capture", help="headless audio capture client (Linux)")
    c = cap.add_subparsers(dest="capture_cmd", required=True)

    p = c.add_parser("configure", help="save the server, device token and mode")
    p.add_argument("--server")
    p.add_argument("--token")
    p.add_argument("--token-file", help="read the token from this file instead of the command line")
    p.add_argument("--mode", help="stream (upload to the server) or local (never leave this box)")
    p.add_argument("--device", help="name this box reports to the server")
    p.add_argument("--source", help="audio source name (see `pactl list short sources`)")

    p = c.add_parser("start", help="start capturing in the background")
    p.add_argument("--title")
    p.add_argument("--meeting", action="store_true", help="a meeting rather than a solo session")
    p.add_argument("--system", action="store_true", help="also capture what this box plays")
    p.add_argument("--local-only", action="store_true", help="record to disk, send nothing")
    p.add_argument("--source")
    p.add_argument("--resume", action="store_true", help="continue the last unfinished session")
    p.add_argument(
        "--resume-or-new",
        action="store_true",
        help="resume if possible, and roll to a new session every max_session_hours (for systemd)",
    )
    p.add_argument("--foreground", action="store_true", help="run in this terminal (for systemd)")

    c.add_parser("stop", help="finish the session and wait for the server")

    p = c.add_parser("status", help="show what the daemon is doing")
    p.add_argument("--json", action="store_true")

    p = c.add_parser("tail", help="print the live transcript")
    p.add_argument(
        "--session", help="follow this session from the server instead of the local daemon"
    )
    p.add_argument("--no-follow", action="store_true", help="print what exists and exit")


def run(args: argparse.Namespace, env: Mapping[str, str] = os.environ) -> int:
    handler = {
        "configure": _configure,
        "start": _start,
        "stop": _stop,
        "status": _status,
        "tail": _tail,
    }[args.capture_cmd]
    try:
        return handler(args, env)
    except ConfigError as e:
        print(f"jarvis-live: {e}", file=sys.stderr)
        return EXIT_USAGE
    except capture.CaptureError as e:
        print(f"jarvis-live: {e}", file=sys.stderr)
        return EXIT_FAIL
    except KeyboardInterrupt:
        return 130


def _sock(env: Mapping[str, str]) -> Path:
    return runtime_dir(env) / "ctl.sock"


def _ask(env: Mapping[str, str], cmd: str, timeout: float = 10.0) -> dict[str, Any] | None:
    """The daemon's reply, or None if no daemon is running."""
    if not _sock(env).exists():
        return None
    try:
        return asyncio.run(control.request(_sock(env), {"cmd": cmd}, timeout))
    except (OSError, control.ControlError):
        return None


# --- configure -----------------------------------------------------------------------------


def _configure(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    cfg = load_config(env, include_env=False)  # never persist env overrides into the file
    if args.mode is not None:
        if args.mode not in MODES:
            raise ConfigError(f"mode must be one of {', '.join(MODES)}, not {args.mode!r}")
        cfg.mode = args.mode
    for name in ("server", "device", "source"):
        if (v := getattr(args, name)) is not None:
            setattr(cfg, name, v)
    if args.token_file:
        cfg.token = Path(args.token_file).read_text().strip()
    elif args.token is not None:
        cfg.token = args.token
    path = save_config(cfg, env)
    print(f"saved {path} (mode: {cfg.mode or 'not set'})")
    return EXIT_OK


# --- start ---------------------------------------------------------------------------------


def _effective_config(args: argparse.Namespace, env: Mapping[str, str]) -> ClientConfig:
    cfg = load_config(env)
    if args.local_only:
        cfg.mode = "local"
    if args.source:
        cfg.source = args.source
    cfg.validate()
    return cfg


def _start(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    cfg = _effective_config(args, env)
    capture.find_tool()  # fail now, with install advice, not inside the detached process
    if args.foreground:
        return _run_foreground(args, cfg, env)

    if _ask(env, "status") is not None:
        print("jarvis-live: already running (see `jarvis-live capture status`)", file=sys.stderr)
        return EXIT_FAIL
    log_path = state_dir(env) / "daemon.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "jarvis_live.cli", "capture", "start", "--foreground"]
    cmd += ["--local-only"] if args.local_only else []
    cmd += ["--meeting"] if args.meeting else []
    cmd += ["--system"] if args.system else []
    cmd += ["--resume"] if args.resume else []
    cmd += ["--resume-or-new"] if args.resume_or_new else []
    cmd += ["--title", args.title] if args.title else []
    cmd += ["--source", args.source] if args.source else []
    with log_path.open("ab") as log:
        child = subprocess.Popen(  # noqa: S603
            cmd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,  # survives the SSH session ending
        )
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if (rc := child.poll()) is not None:
            print(f"jarvis-live: capture failed to start (exit {rc}):", file=sys.stderr)
            print(_tail_log(log_path), file=sys.stderr)
            return EXIT_FAIL
        st = _ask(env, "status", timeout=2.0)
        if st is not None and st.get("state") == "recording":
            _print_started(st)
            return EXIT_OK
        if st is not None and st.get("state") in ("failed", "no audio"):
            print(f"jarvis-live: {st['state']}: {st.get('problem')}", file=sys.stderr)
            _ask(env, "stop", timeout=30.0)  # don't leave a daemon running that records nothing
            return EXIT_FAIL
        time.sleep(0.2)
    print(f"jarvis-live: timed out starting; see {log_path}", file=sys.stderr)
    return EXIT_FAIL


def _print_started(st: dict[str, Any]) -> None:
    print(_mode_line(st))
    print(f"session   {st['session']}")
    if st.get("dashboard_url"):
        print(f"dashboard {st['dashboard_url']}")
    print("capturing. `jarvis-live capture tail` to watch, `... stop` to finish.")


def _mode_line(st: dict[str, Any]) -> str:
    note = f" ({st['mode_note']})" if st.get("mode_note") else ""
    return f"{st['mode']}{note}"


def _tail_log(path: Path, lines: int = 8) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def _run_foreground(args: argparse.Namespace, cfg: ClientConfig, env: Mapping[str, str]) -> int:
    from jarvis_live.client.daemon import Daemon, SessionOptions

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    opts = SessionOptions(
        title=args.title,
        meeting=args.meeting,
        system=args.system,
        resume=args.resume or args.resume_or_new,
        roll=args.resume_or_new,
    )
    daemon = Daemon(cfg, env, opts)

    async def main() -> int:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, daemon.request_stop)
        return await daemon.run()

    try:
        return asyncio.run(main())
    except RuntimeError as e:
        print(f"jarvis-live: {e}", file=sys.stderr)
        return EXIT_FAIL


# --- stop / status -------------------------------------------------------------------------


def _stop(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    if _ask(env, "status") is None:
        print("jarvis-live: not running", file=sys.stderr)
        return EXIT_NOT_RUNNING
    print("stopping: flushing audio and waiting for the server...")
    try:
        reply = asyncio.run(control.request(_sock(env), {"cmd": "stop"}, timeout=120.0))
    except (OSError, control.ControlError) as e:
        print(f"jarvis-live: {e}", file=sys.stderr)
        return EXIT_FAIL
    if reply.get("ended"):
        print(f"session {reply.get('session')} ended")
        note = reply.get("final_note")
        if note:
            print(f"final note: {note.get('title')} ({note.get('path')})")
        return EXIT_OK
    print(
        f"session {reply.get('session')} stopped but not confirmed by the server.\n"
        "The recording is kept on disk; `jarvis-live capture start --resume` continues it.",
        file=sys.stderr,
    )
    return EXIT_FAIL


def _hms(seconds: int) -> str:
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _status(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    st = _ask(env, "status")
    if st is None:
        if args.json:
            print(json.dumps({"state": "not running"}))
        else:
            print("not running")
        return EXIT_NOT_RUNNING
    if args.json:
        print(json.dumps(st))
        return EXIT_OK
    print(_mode_line(st))
    print(f"session    {st.get('session')}" + (f'  "{st["title"]}"' if st.get("title") else ""))
    state = f"{st['state']} ({st.get('connection')})"
    print(f"state      {state}")
    if st.get("problem"):
        print(f"problem    {st['problem']}")
    print(f"uptime     {_hms(st.get('uptime_s', 0))}")
    print(f"unsent     {st.get('unacked_ms', 0) / 1000:.1f} s of audio")
    if st.get("dashboard_url"):
        print(f"dashboard  {st['dashboard_url']}")
    print(f"device     {st['device']}   source {st.get('source') or 'default'}")
    return EXIT_OK


# --- tail ----------------------------------------------------------------------------------


def _tail(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    if args.session:
        return asyncio.run(_tail_viewer(args, load_config(env)))
    st = _ask(env, "status")
    if st is None:
        print("jarvis-live: not running", file=sys.stderr)
        return EXIT_NOT_RUNNING
    if st["mode"] == "LOCAL ONLY":
        print("local-only: no transcript (audio is kept on this machine; nothing is transcribed)")
        return EXIT_OK
    return asyncio.run(_tail_daemon(args, env))


def _emit(renderer: TailRenderer, msg: ServerMessage) -> None:
    for line in renderer.render(msg):
        print(line, flush=True)


async def _tail_daemon(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    renderer = TailRenderer()
    from jarvis_live.client.transport import parse_server_message

    async def follow() -> None:
        async for event in control.subscribe(_sock(env)):
            if (msg := parse_server_message(json.dumps(event))) is not None:
                _emit(renderer, msg)

    try:
        if args.no_follow:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(follow(), 1.0)  # backlog arrives immediately
        else:
            await follow()
    except OSError as e:
        print(f"jarvis-live: {e}", file=sys.stderr)
        return EXIT_FAIL
    return EXIT_OK


async def _tail_viewer(args: argparse.Namespace, cfg: ClientConfig) -> int:
    from jarvis_live.client.transport import fetch_session, parse_server_message, viewer_events

    if not (cfg.server and cfg.token):
        raise ConfigError("tail --session needs a server and token: run `capture configure`")
    renderer = TailRenderer()
    detail = await fetch_session(cfg.server, cfg.token, args.session)
    seen: set[str] = set()
    for seg in detail.get("segments", []):
        msg = parse_server_message(json.dumps({"type": "segment", **seg}))
        if isinstance(msg, Segment):
            seen.add(msg.id)
            _emit(renderer, msg)
    if args.no_follow or detail.get("status") == "done":
        return EXIT_OK
    async for msg in viewer_events(cfg.server, cfg.token, args.session):
        if isinstance(msg, Segment) and msg.id in seen:
            continue
        _emit(renderer, msg)
    return EXIT_OK


__all__ = ["add_parser", "config_dir", "run"]
