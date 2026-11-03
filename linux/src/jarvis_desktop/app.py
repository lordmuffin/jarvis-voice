"""Entry point for ``jarvis-voice``."""

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx
from jarvis_live.client.config import load_config

from jarvis_desktop import __version__, evalset

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

    args = parser.parse_args(argv)
    if args.cmd == "eval":
        return _eval_pull(args, env)
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
