import asyncio
import re
import wave
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live import cli
from jarvis_live.auth import hash_token
from jarvis_live.config import get_settings
from jarvis_live.db.models import Device
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
    assert token[1] not in device.token_hash


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
