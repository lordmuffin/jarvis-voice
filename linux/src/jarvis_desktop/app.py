"""Entry point for ``jarvis-voice``."""

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx
from jarvis_live.client.config import load_config

from jarvis_desktop import __version__, evalset, readset
from jarvis_desktop.stt import engine as engine_mod
from jarvis_desktop.stt import models
from jarvis_desktop.stt import score as score_mod
from jarvis_desktop.stt.engine import Engine, EngineError

#: The default first (plan section 2.2).
ENGINES = ["whisper-turbo", "parakeet"]
MODES = {"solo": frozenset({"solo"}), "meeting": frozenset({"meeting"})}
MODES["any"] = MODES["solo"] | MODES["meeting"]


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(prog="jarvis-voice")
    parser.add_argument("--version", action="version", version=f"jarvis-voice {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    ev = sub.add_parser("eval", help="speech-to-text evaluation data").add_subparsers(
        dest="eval_cmd", required=True
    )
    p = ev.add_parser(
        "pull",
        help="download your own mic utterances and the server's transcript of them",
        description=evalset.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--server", help="default: the capture client's config (JARVIS_LIVE_SERVER)")
    p.add_argument("--token-file", type=Path, help="a file holding a device token")
    p.add_argument("--out", type=Path, help="default: $XDG_DATA_HOME/jarvis-voice/eval")
    p.add_argument("--max-clips", type=int, default=200)
    p.add_argument("--mode", choices=sorted(MODES), default="solo")
    p.add_argument("--include-restricted", action="store_true")

    p = ev.add_parser("score", help="run an on-device engine on the eval set and score it")
    p.add_argument(
        "--engine",
        choices=ENGINES,
        default=ENGINES[0],
        help="scored alone, without the fallback dictation uses (default: %(default)s)",
    )
    p.add_argument("--eval-dir", type=Path, help="default: $XDG_DATA_HOME/jarvis-voice/eval")
    p.add_argument("--limit", type=int, help="score only the first N clips")

    p = ev.add_parser(
        "record",
        help="read known sentences into your mic: an eval set with exact references",
        description=readset.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--source",
        help="mic to record: a `pactl list short sources` name on Linux, an AVFoundation "
        "input name or index on macOS (default: the system default input)",
    )
    p.add_argument("--out", type=Path, help="default: $XDG_DATA_HOME/jarvis-voice/eval-read")

    md = sub.add_parser("models", help="on-device speech models").add_subparsers(
        dest="models_cmd", required=True
    )
    md.add_parser("install", help="download and verify the pinned models (about 1.12 GB)")

    args = parser.parse_args(argv)
    if args.cmd == "eval":
        commands = {"pull": _eval_pull, "score": _eval_score, "record": _eval_record}
        return commands[args.eval_cmd](args, env)
    if args.cmd == "models":
        return _models_install(env)
    return 0


def load_engine(name: str, env: Mapping[str, str]) -> Engine:
    """One engine on its own (no fallback), so a score says which engine produced it."""
    root = models.models_dir(env)
    if name == "parakeet":
        return engine_mod.ParakeetEngine(root / models.PARAKEET_TDT_V2.name)
    spec = models.WHISPER_TURBO_Q5
    return engine_mod.WhisperCppEngine(root / spec.name / spec.files[0])


def _eval_score(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    try:
        engine = load_engine(args.engine, env)
    except EngineError as e:
        print(f"jarvis-voice: {e}. Run `jarvis-voice models install` first.", file=sys.stderr)
        return 1
    eval_dir = args.eval_dir or evalset.default_output_dir(env)
    try:
        report = score_mod.score(engine, eval_dir, limit=args.limit)
    except FileNotFoundError as e:
        print(f"jarvis-voice: {e}", file=sys.stderr)
        return 1
    print(score_mod.format_report(report))
    return 0


def _eval_record(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    out = args.out or readset.default_output_dir(env)
    try:
        recorder = readset.SubprocessRecorder(readset.recorder_command(args.source))
        readset.record(
            readset.prompts(),
            out,
            recorder,
            ask=input,
            say=print,
            mic=readset.describe_source(args.source),
        )
    except (readset.RecordError, evalset.PullError) as e:
        print(f"jarvis-voice: {e}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nStopped; run it again to carry on.")
    print(f"Score it with: jarvis-voice eval score --eval-dir {out}")
    return 0


def _models_install(env: Mapping[str, str]) -> int:
    root = models.models_dir(env)
    for spec in models.ALL:
        shown = [-1]

        def progress(
            fraction: float, spec: models.ModelSpec = spec, shown: list[int] = shown
        ) -> None:
            pct = int(fraction * 100)
            if pct // 10 != shown[0] // 10:
                shown[0] = pct
                print(f"\r{spec.name}: {pct}%", end="", flush=True)

        try:
            path = models.install(spec, root, progress=progress)
        except (models.ModelError, httpx.HTTPError) as e:
            print(f"\njarvis-voice: {e}", file=sys.stderr)
            return 1
        print(f"\r{spec.name}: installed in {path}")
    return 0


def _eval_pull(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    cfg = load_config(env)
    server = args.server or cfg.server
    token = args.token_file.read_text().strip() if args.token_file else cfg.token
    if not server or not token:
        print(
            "jarvis-voice: need a server and a device token: pass --server URL and "
            "--token-file FILE (create a token on the dashboard's Devices page), or set "
            "JARVIS_LIVE_SERVER and JARVIS_LIVE_TOKEN_FILE",
            file=sys.stderr,
        )
        return 2
    out = args.out or evalset.default_output_dir(env)

    async def run() -> evalset.Summary:
        async with httpx.AsyncClient(
            base_url=server.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=60.0,
        ) as client:
            return await evalset.pull(
                client,
                out,
                max_clips=args.max_clips,
                modes=MODES[args.mode],
                include_restricted=args.include_restricted,
            )

    try:
        summary = asyncio.run(run())
    except (evalset.PullError, httpx.HTTPError) as e:
        print(f"jarvis-voice: {e}", file=sys.stderr)
        return 1
    print(
        f"{summary.clips} new clips from {summary.sessions} sessions "
        f"({summary.skipped} already there) in {out}"
    )
    return 0
