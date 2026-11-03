"""``jarvis-live stream-key create|list|revoke``: keys for streams pushed in (docs/obs.md)."""

import argparse
import sys
from typing import Any

from jarvis_live.streams import SOURCES


def add_parser(sub: Any) -> None:
    p = sub.add_parser("stream-key", help="keys for OBS / phone streams pushed into MediaMTX")
    c = p.add_subparsers(dest="stream_cmd", required=True)
    m = c.add_parser("create", help="create the key for a stream path and print it once")
    m.add_argument("name", help="the stream path: 1-40 of a-z, 0-9 and '-'")
    m.add_argument("--meeting", action="store_true", help="its sessions are meetings, not solo")
    m.add_argument("--title", help="title of its sessions (default: the copilot names them)")
    m.add_argument("--restricted", action="store_true", help="a work stream: kept on the homelab")
    m.add_argument("--source", choices=SOURCES, default="obs")
    m.add_argument("--user", help="email or id of the user it belongs to (default: the owner)")
    m.add_argument("--host", default="<ingest-host>", help="MediaMTX's address, for the hints")
    c.add_parser("list", help="list stream keys (never the keys themselves)")
    o = c.add_parser("rotate", help="replace a stream's key with a new one, same settings")
    o.add_argument("name")
    o.add_argument("--host", default="<ingest-host>", help="MediaMTX's address, for the hints")
    r = c.add_parser("revoke", help="revoke a stream's key")
    r.add_argument("name")


def run(args: argparse.Namespace, with_db: Any) -> int:
    if args.stream_cmd == "create":
        return int(with_db(lambda sm: _create(sm, args)))
    if args.stream_cmd == "list":
        return int(with_db(_list))
    if args.stream_cmd == "rotate":
        return int(with_db(lambda sm: _rotate(sm, args)))
    return int(with_db(lambda sm: _revoke(sm, args.name)))


async def _create(sm: Any, args: argparse.Namespace) -> int:
    from jarvis_live.db.models import OWNER_USER_ID
    from jarvis_live.streams import keys
    from jarvis_live.users import find_user

    user_id = OWNER_USER_ID
    if args.user is not None:
        async with sm() as db:
            user = await find_user(db, args.user)
        if user is None:
            print(f"no user {args.user!r} (they must sign in once first)", file=sys.stderr)
            return 1
        user_id = user.id
    try:
        secret = await keys.create_key(
            sm,
            args.name,
            user_id,
            mode="meeting" if args.meeting else "solo",
            title=args.title,
            restricted=args.restricted,
            source=args.source,
        )
    except keys.IngestKeyError as e:
        print(f"jarvis-live: {e}", file=sys.stderr)
        return 1
    _print_key(args.name, secret, args.host)
    return 0


async def _list(sm: Any) -> int:
    from jarvis_live.streams import keys

    for k in await keys.list_keys(sm):
        state = "revoked" if k.revoked_at else "active"
        flags = " restricted" if k.restricted else ""
        print(f"{k.name:<40} {state:<8} {k.source:<6} {k.mode:<7}{flags}  {k.title or ''}".rstrip())
    return 0


async def _revoke(sm: Any, name: str) -> int:
    from jarvis_live.streams import keys

    if await keys.revoke_key(sm, name):
        print(f"revoked {name}")
        return 0
    print(f"jarvis-live: no active stream key {name!r}", file=sys.stderr)
    return 1


async def _rotate(sm: Any, args: argparse.Namespace) -> int:
    from jarvis_live.streams import keys

    try:
        secret = await keys.rotate_key(sm, args.name)
    except keys.IngestKeyError as e:
        print(f"jarvis-live: {e}", file=sys.stderr)
        return 1
    _print_key(args.name, secret, args.host)
    print(
        "The old key no longer works. A stream running on it continues until it reconnects.",
        file=sys.stderr,
    )
    return 0


def _print_key(n: str, secret: str, h: str) -> None:
    print(f"stream: {n}")
    print(f"key:    {secret}")
    print()
    print(
        f"OBS, SRT (Settings > Stream > Custom), Server:\n  srt://{h}:8890?streamid=publish:{n}:obs:{secret}&pkt_size=1316"
    )
    print(f"OBS, RTMP: Server rtmp://{h}:1935  Stream Key {n}?user=obs&pass={secret}")
    print("Store this key now; it is not shown again.", file=sys.stderr)
