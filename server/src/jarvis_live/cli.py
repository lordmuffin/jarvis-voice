import argparse
import asyncio
import sys
from pathlib import Path

from jarvis_live.config import get_settings


def _create_device(name: str) -> int:
    from jarvis_live.auth import create_device
    from jarvis_live.db.session import make_engine, make_sessionmaker

    async def go() -> None:
        engine = make_engine(get_settings().database_url)
        try:
            device_id, token = await create_device(make_sessionmaker(engine), name)
        finally:
            await engine.dispose()
        print(f"device id: {device_id}")
        print(f"token:     {token}")
        print("Store this token now; it is not shown again.", file=sys.stderr)
        print(f"Use it as: jarvis-live replay --server <url> --token={token} ...", file=sys.stderr)

    asyncio.run(go())
    return 0


def _replay(args: argparse.Namespace) -> int:
    from jarvis_live.replay import replay

    res = asyncio.run(replay(args.server, args.token, Path(args.wav), args.channel, args.speed))
    segments = [e for e in res.events if e.get("type") == "segment"]
    print(f"session {res.session_id}: sent {res.sent_frames} frames, acked {res.acked}")
    for s in segments:
        print(f"  [{s['start_ms']:>7}-{s['end_ms']:>7}] {s['speaker']}: {s['text']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis-live")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create-device", help="create a device and print its token once")
    p.add_argument("--name", required=True)

    p = sub.add_parser("serve", help="run the server")
    p.add_argument("--host", default="0.0.0.0")  # noqa: S104
    p.add_argument("--port", type=int, default=8000)

    sub.add_parser("migrate", help="apply database migrations")

    p = sub.add_parser("replay", help="stream a WAV file to a server as a producer")
    p.add_argument("--server", required=True)
    p.add_argument("--token", required=True)
    p.add_argument("--wav", required=True)
    p.add_argument("--channel", choices=["mic", "system"], default="mic")
    p.add_argument("--speed", type=float, default=4.0)

    args = parser.parse_args(argv)
    if args.cmd == "create-device":
        return _create_device(args.name)
    if args.cmd == "migrate":
        from jarvis_live.db.session import run_migrations

        run_migrations(get_settings().database_url)
        return 0
    if args.cmd == "serve":
        import uvicorn

        from jarvis_live.logging_setup import configure_logging

        configure_logging(get_settings().log_level)
        uvicorn.run("jarvis_live.app:app", host=args.host, port=args.port, log_config=None)
        return 0
    return _replay(args)


if __name__ == "__main__":
    raise SystemExit(main())
