"""Whisper tier list: parsing, health probing, and fall-through requests."""

import asyncio
import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)


class STTUnavailable(Exception):
    """No configured tier could transcribe the segment."""


@dataclass(frozen=True)
class Tier:
    name: str
    endpoint: str  # no trailing slash
    model: str


def parse_tiers(spec: str) -> list[Tier]:
    """Parse ``WHISPER_TIERS``: one ``<name> <endpoint> <model>`` per line, highest priority first.

    Blank lines and lines starting with ``#`` are ignored.
    """
    tiers: list[Tier] = []
    for n, raw in enumerate(spec.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            raise ValueError(
                f"WHISPER_TIERS line {n}: expected '<name> <endpoint> <model>': {raw!r}"
            )
        name, endpoint, model = parts
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError(f"WHISPER_TIERS line {n}: endpoint must be http(s): {endpoint!r}")
        if any(t.name == name for t in tiers):
            raise ValueError(f"WHISPER_TIERS line {n}: duplicate tier name {name!r}")
        tiers.append(Tier(name, endpoint.rstrip("/"), model))
    return tiers


class TierPool:
    def __init__(
        self,
        tiers: list[Tier],
        client: httpx.AsyncClient,
        *,
        probe_interval_s: float = 30.0,
        probe_timeout_s: float = 5.0,
        request_timeout_s: float = 60.0,
    ) -> None:
        self.tiers = tiers
        self._client = client
        self._probe_interval = probe_interval_s
        self._probe_timeout = probe_timeout_s
        self._request_timeout = request_timeout_s
        self._healthy: dict[str, bool] = {t.name: False for t in tiers}
        self._probed = False

    def current_tier(self) -> str | None:
        """Name of the first healthy tier."""
        return next((t.name for t in self.tiers if self._healthy[t.name]), None)

    async def probe_all(self) -> None:
        results = await asyncio.gather(*(self._probe(t) for t in self.tiers))
        for tier, ok in zip(self.tiers, results, strict=True):
            if ok != self._healthy[tier.name]:
                log.info("stt tier %s is now %s", tier.name, "healthy" if ok else "unhealthy")
            self._healthy[tier.name] = ok
        self._probed = True

    async def run_probe_loop(self) -> None:
        while True:
            await self.probe_all()
            await asyncio.sleep(self._probe_interval)

    async def _probe(self, tier: Tier) -> bool:
        for path in ("/models", "/health"):
            try:
                r = await self._client.get(tier.endpoint + path, timeout=self._probe_timeout)
            except httpx.HTTPError:
                continue
            if r.is_success:
                return True
        return False

    async def transcribe(self, wav: bytes, prompt: str) -> tuple[str, str]:
        """Return ``(text, tier name)``. Tries healthy tiers in order, falling through to the
        next on any request error. If none are healthy per the last probe (which may be up to
        one interval stale) every tier is tried in order as a last resort."""
        if not self._probed:
            await self.probe_all()
        candidates = [t for t in self.tiers if self._healthy[t.name]] or self.tiers
        for tier in candidates:
            try:
                return await self._request(tier, wav, prompt), tier.name
            except (httpx.HTTPError, ValueError, KeyError) as e:
                log.warning("stt tier %s failed: %r", tier.name, e)
                self._healthy[tier.name] = False
        raise STTUnavailable("no STT tier could transcribe the segment")

    async def _request(self, tier: Tier, wav: bytes, prompt: str) -> str:
        data = {"model": tier.model, "language": "en"}
        if prompt:
            data["prompt"] = prompt
        r = await self._client.post(
            tier.endpoint + "/audio/transcriptions",
            data=data,
            files={"file": ("audio.wav", wav, "audio/wav")},
            timeout=self._request_timeout,
        )
        r.raise_for_status()
        text = r.json()["text"]
        if not isinstance(text, str):
            raise ValueError("transcription 'text' is not a string")
        return text.strip()
