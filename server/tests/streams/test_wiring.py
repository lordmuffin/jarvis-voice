"""Stream ingest inside the app: off unless enabled, its auth hook on a port of its own."""

import asyncio
import re
import socket
import uuid
from collections.abc import Callable

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live import cli
from jarvis_live.auth import TicketStore
from jarvis_live.config import Settings, get_settings
from jarvis_live.db.models import OWNER_USER_ID
from jarvis_live.streams import keys
from jarvis_live.streams.wiring import build
from tests.conftest import ServerHandle, _free_port

SM = async_sessionmaker  # type: ignore[type-arg]
RUN = uuid.uuid4().hex[:6]


def _listening(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def test_streams_are_off_by_default() -> None:
    cfg = Settings(database_url="postgresql+asyncpg://x/y")
    assert cfg.streams_enabled is False
    assert cfg.streams_stop_grace_s == 60.0  # covers a 30 s network drop


async def test_enabled_serves_the_auth_hook_on_its_own_port_only(
    make_server: Callable[..., ServerHandle], sm: SM
) -> None:
    port = _free_port()
    server = make_server(
        streams_enabled=True,
        streams_auth_port=port,
        streams_mediamtx_api=f"http://127.0.0.1:{_free_port()}",  # nothing there: harmless
    )
    name = f"wired-{RUN}"
    secret = await keys.create_key(sm, name, OWNER_USER_ID)
    body = {"action": "publish", "path": name, "user": "obs", "password": secret}
    async with httpx.AsyncClient() as c:
        for _ in range(50):
            if _listening(port):
                break
            await asyncio.sleep(0.1)
        assert (
            await c.post(f"http://127.0.0.1:{port}/mediamtx/auth", json=body)
        ).status_code == 200
        bad = body | {"password": "nope"}
        assert (await c.post(f"http://127.0.0.1:{port}/mediamtx/auth", json=bad)).status_code == 401
        # Never on the app port, which the Ingress routes to the internet.
        assert (await c.post(f"{server.url}/mediamtx/auth", json=body)).status_code in (404, 405)
        assert (await c.get(f"{server.url}/healthz")).status_code == 200  # MediaMTX away: fine


async def test_disabled_opens_no_auth_port(make_server: Callable[..., ServerHandle]) -> None:
    port = _free_port()
    make_server(streams_auth_port=port)
    await asyncio.sleep(0.3)
    assert not _listening(port)


async def test_the_ingest_mints_producer_tickets_for_the_keys_owner(
    sm: SM, tmp_path: object
) -> None:
    cfg = Settings(database_url="postgresql+asyncpg://x/y", data_dir=tmp_path)  # type: ignore[arg-type]
    tickets = TicketStore()
    async with httpx.AsyncClient() as client:
        ingest, _auth_server = build(cfg, sm, tickets, client)
        sid, user = uuid.uuid4(), uuid.uuid4()
        ticket = await ingest._ticket_for(sid, user)
    redeemed = tickets.redeem(ticket, sid)
    assert redeemed is not None and (redeemed.role, redeemed.user_id) == ("producer", user)


async def test_cli_creates_lists_and_revokes_stream_keys(
    postgres_url: str, sm: SM, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("JARVIS_LIVE_DATABASE_URL", postgres_url)
    name = f"cli-{RUN}"
    get_settings.cache_clear()
    try:
        argv = ["stream-key", "create", name, "--meeting", "--title", "Desk", "--restricted"]
        assert await asyncio.to_thread(cli.main, argv) == 0
        out = capsys.readouterr().out
        secret = re.search(r"key:\s+(\S+)", out)
        assert secret is not None
        assert f"streamid=publish:{name}:obs:{secret[1]}" in out  # the SRT URL to paste
        found = await keys.verify(sm, name, secret[1])
        assert found is not None and (found.mode, found.title, found.restricted) == (
            "meeting",
            "Desk",
            True,
        )

        assert await asyncio.to_thread(cli.main, ["stream-key", "list"]) == 0
        listed = capsys.readouterr().out
        assert name in listed and secret[1] not in listed  # never shown again

        assert await asyncio.to_thread(cli.main, ["stream-key", "create", name]) == 1  # taken
        assert await asyncio.to_thread(cli.main, ["stream-key", "create", "Bad Name"]) == 1
        rotate = ["stream-key", "rotate", name, "--host", "ingest.example"]
        assert await asyncio.to_thread(cli.main, rotate) == 0
        out = capsys.readouterr().out
        rotated = re.search(r"key:\s+(\S+)", out)
        assert rotated is not None and rotated[1] != secret[1]
        assert "rtmp://ingest.example:1935" in out  # the settings to paste, as on create
        assert await keys.verify(sm, name, secret[1]) is None
        assert await keys.verify(sm, name, rotated[1]) is not None

        assert await asyncio.to_thread(cli.main, ["stream-key", "revoke", name]) == 0
        assert await keys.verify(sm, name, rotated[1]) is None
        assert await asyncio.to_thread(cli.main, ["stream-key", "revoke", name]) == 1
        assert await asyncio.to_thread(cli.main, ["stream-key", "rotate", name]) == 1
    finally:
        get_settings.cache_clear()
