import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any


def _with_db(fn: Callable[[Any], Awaitable[int]]) -> int:
    """Run ``fn(sessionmaker)`` against the configured database."""
    from jarvis_live.config import get_settings
    from jarvis_live.db.session import make_engine, make_sessionmaker

    async def go() -> int:
        engine = make_engine(get_settings().database_url)
        try:
            return await fn(make_sessionmaker(engine))
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _create_device(name: str, user_ref: str | None) -> int:
    from jarvis_live.auth import create_device
    from jarvis_live.db.models import OWNER_USER_ID
    from jarvis_live.users import find_user

    async def go(sm: Any) -> int:
        user_id = OWNER_USER_ID
        if user_ref is not None:
            async with sm() as db:
                user = await find_user(db, user_ref)
            if user is None:
                print(f"no user {user_ref!r} (they must sign in once first)", file=sys.stderr)
                return 1
            user_id = user.id
        device_id, token = await create_device(sm, name, user_id)
        print(f"device id: {device_id}")
        print(f"token:     {token}")
        print("Store this token now; it is not shown again.", file=sys.stderr)
        print(f"Use it as: jarvis-live replay --server <url> --token={token} ...", file=sys.stderr)
        return 0

    return _with_db(go)


def _list_users() -> int:
    from sqlalchemy import select

    from jarvis_live.db.models import User

    async def go(sm: Any) -> int:
        async with sm() as db:
            users = (await db.execute(select(User).order_by(User.created_at))).scalars().all()
        for u in users:
            flags = " admin" if u.is_admin else ""
            signed = "" if u.oidc_sub else " (not signed in yet)"
            print(f"{u.id}  {u.email or '-':<32} {u.display_name}{flags}{signed}")
        return 0

    return _with_db(go)


def _set_admin(user_ref: str, on: bool) -> int:
    from jarvis_live.users import find_user

    async def go(sm: Any) -> int:
        async with sm() as db:
            user = await find_user(db, user_ref)
            if user is None:
                print(f"no user {user_ref!r}", file=sys.stderr)
                return 1
            user.is_admin = on
            await db.commit()
        print(f"{user.display_name}: admin={'yes' if on else 'no'}")
        return 0

    return _with_db(go)


def _claim_owner(email: str) -> int:
    from jarvis_live.users import claim_owner

    async def go(sm: Any) -> int:
        try:
            owner = await claim_owner(sm, email)
        except LookupError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(f"{owner.display_name} <{owner.email}> now owns the pre-existing devices/sessions")
        return 0

    return _with_db(go)


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
    p.add_argument("--user", help="email or id of the user it belongs to (default: the owner)")

    sub.add_parser("list-users", help="list users")

    p = sub.add_parser("set-admin", help="make a user an admin (or not, with --off)")
    p.add_argument("user", help="email or id")
    p.add_argument("--off", action="store_true")

    p = sub.add_parser(
        "claim-owner",
        help="give the devices and sessions from before sign-in to the user with this email",
    )
    p.add_argument("email")

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

    from jarvis_live.client import capture_cli

    capture_cli.add_parser(sub)

    args = parser.parse_args(argv)
    if args.cmd == "capture":
        return capture_cli.run(args)
    if args.cmd == "create-device":
        return _create_device(args.name, args.user)
    if args.cmd == "list-users":
        return _list_users()
    if args.cmd == "set-admin":
        return _set_admin(args.user, not args.off)
    if args.cmd == "claim-owner":
        return _claim_owner(args.email)
    if args.cmd == "migrate":
        from jarvis_live.config import get_settings
        from jarvis_live.db.session import run_migrations

        run_migrations(get_settings().database_url)
        return 0
    if args.cmd == "serve":
        import uvicorn

        from jarvis_live.config import get_settings
        from jarvis_live.logging_setup import configure_logging

        configure_logging(get_settings().log_level)
        uvicorn.run("jarvis_live.app:app", host=args.host, port=args.port, log_config=None)
        return 0
    return _replay(args)


if __name__ == "__main__":
    raise SystemExit(main())
