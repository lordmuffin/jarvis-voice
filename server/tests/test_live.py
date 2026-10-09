"""Hits a real Whisper endpoint. Run with ``--live`` and JARVIS_LIVE_WHISPER_TIERS set."""

import httpx
import pytest

from jarvis_live.config import Settings
from jarvis_live.stt.backend import WhisperBackend
from jarvis_live.stt.tiers import TierPool, parse_tiers
from tests.audio import silence, tone

pytestmark = pytest.mark.live


async def test_real_whisper_roundtrip() -> None:
    tiers = parse_tiers(Settings().whisper_tiers)
    assert tiers, "set JARVIS_LIVE_WHISPER_TIERS"
    async with httpx.AsyncClient() as client:
        pool = TierPool(tiers, client)
        await pool.probe_all()
        assert pool.current_tier() is not None
        result = await WhisperBackend(pool).transcribe(silence(0.5) + tone(1.0) + silence(0.5), "")
    assert result.tier == pool.current_tier()
    assert isinstance(result.text, str)
