import asyncio
import re
import uuid
import wave
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live import cli
from jarvis_live.auth import hash_token
from jarvis_live.config import get_settings
from jarvis_live.db.models import OWNER_USER_ID, Device, User
from jarvis_live.users import create_user
from tests.audio import TWO_UTTERANCES
from tests.conftest import ServerHandle


async def test_create_device_prints_token_once_and_stores_only_its_hash(
    postgres_url: str,
    sm: async_sessionmaker,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("JARVIS_LIVE_DATABASE_URL", postgres_url)
    get_settings.cache_clear()
    try:
        assert await asyncio.to_thread(cli.main, ["create-device", "--name", "kitchen-mac"]) == 0
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    token = re.search(r"token:\s+(\S+)", out)
    assert token is not None
    async with sm() as db:
        device = (
            await db.execute(select(Device).where(Device.token_hash == hash_token(token[1])))
        ).scalar_one()
    assert device.name == "kitchen-mac" and device.revoked_at is None
    assert device.user_id == OWNER_USER_ID  # no --user: the deployment owner's
    assert token[1] not in device.token_hash


async def test_user_admin_commands(
    postgres_url: str,
    sm: async_sessionmaker,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    email = f"cli-{uuid.uuid4().hex[:8]}@example.com"
    user = await create_user(sm, "Cli User", email=email, oidc_sub=f"sub-{email}")
    monkeypatch.setenv("JARVIS_LIVE_DATABASE_URL", postgres_url)
    get_settings.cache_clear()

    def run(*args: str) -> int:
        return cli.main(list(args))

    try:
        assert await asyncio.to_thread(run, "create-device", "--name", "m", "--user", email) == 0
        assert await asyncio.to_thread(run, "create-device", "--name", "m", "--user", "x@y") == 1
        assert await asyncio.to_thread(run, "set-admin", email.upper()) == 0
        assert await asyncio.to_thread(run, "list-users") == 0
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert f"{email}" in out and "Cli User admin" in out
    async with sm() as db:
        assert (await db.get(User, user.id)).is_admin  # type: ignore[union-attr]
        devices = (await db.execute(select(Device).where(Device.user_id == user.id))).scalars()
        assert [d.name for d in devices] == ["m"]


async def test_replay_cli_streams_a_wav_file(
    server: ServerHandle,
    token: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wav = tmp_path / "in.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(TWO_UTTERANCES)
    # `--token=` form: token_urlsafe can start with "-", which argparse would read as a flag.
    args = [
        "replay",
        "--server",
        server.url,
        f"--token={token}",
        "--wav",
        str(wav),
        "--speed",
        "50",
    ]
    assert await asyncio.to_thread(cli.main, args) == 0
    out = capsys.readouterr().out
    assert "seg-1" in out and "seg-2" in out and "acked {'mic': 76" in out
