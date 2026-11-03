"""Whisper tier list: parsing, health probing, and fall-through requests."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from jarvis_live import metrics

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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.tiers = tiers
        self._client = client
        self._probe_interval = probe_interval_s
        self._probe_timeout = probe_timeout_s
        self._request_timeout = request_timeout_s
        self._clock = clock
        self._healthy: dict[str, bool] = {t.name: False for t in tiers}
        self._probed = False
        self._exhausted = False  # every tier failed since the last probe
        self._answering = False  # a tier answered the last probe
        self._fault: tuple[float, float] | None = None  # first and latest failure while it did

    def current_tier(self) -> str | None:
        """Name of the first healthy tier."""
        return next((t.name for t in self.tiers if self._healthy[t.name]), None)

    def fault_s(self) -> float:
        """How long segments have kept failing while a tier answers its health probe. That is a
        fault, not Whisper being away. A single failure spans 0 s: a tier leaving between two
        probes looks just like this until the next probe."""
        return self._fault[1] - self._fault[0] if self._fault else 0.0

    async def probe_all(self) -> None:
        results = await asyncio.gather(*(self._probe(t) for t in self.tiers))
        for tier, ok in zip(self.tiers, results, strict=True):
            if ok != self._healthy[tier.name]:
                log.info("stt tier %s is now %s", tier.name, "healthy" if ok else "unhealthy")
            self._healthy[tier.name] = ok
        self._probed = True
        self._exhausted = False
        self._answering = any(results)
        if not self._answering:
            self._fault = None  # nothing there: away
        metrics.stt_tiers(self._healthy, self.current_tier())

    async def run_probe_loop(self) -> None:
        while True:
            await self.probe_all()
            await asyncio.sleep(self._probe_interval)

    async def _probe(self, tier: Tier) -> bool:
        # OpenAI-style servers answer under the tier's /v1; whisper-cpp only at its root.
        root = tier.endpoint.removesuffix("/v1")
        for url in (f"{tier.endpoint}/models", f"{tier.endpoint}/health", f"{root}/health"):
            try:
                r = await self._client.get(url, timeout=self._probe_timeout)
            except httpx.HTTPError:
                continue
            if r.is_success:
                return True
        return False

    async def transcribe(self, wav: bytes, prompt: str) -> tuple[str, str]:
        """Return ``(text, tier name)``. Tries healthy tiers in order, falling through to the
        next on any request error. If none are healthy per the last probe (which may be up to
        one interval stale) every tier is tried in order as a last resort, once per probe: with
        Whisper away, a session's queue must not wait on dead tiers segment by segment."""
        if not self._probed:
            await self.probe_all()
        healthy = [t for t in self.tiers if self._healthy[t.name]]
        if not healthy and self._exhausted:
            raise STTUnavailable("no STT tier is up")
        for tier in healthy or self.tiers:
            started = self._clock()
            try:
                text = await self._request(tier, wav, prompt)
            except (httpx.HTTPError, ValueError, KeyError) as e:
                log.warning("stt tier %s failed: %r", tier.name, e)
                self._healthy[tier.name] = False
                outcome = "timeout" if isinstance(e, httpx.TimeoutException) else "error"
                metrics.stt_request(tier.name, outcome, self._clock() - started)
                metrics.stt_fallthrough(tier.name)
                metrics.stt_tiers(self._healthy, self.current_tier())
                continue
            metrics.stt_request(tier.name, "ok", self._clock() - started)
            metrics.stt_served(tier.name)
            self._fault = None
            return text, tier.name
        self._exhausted = True
        if self._answering:
            now = self._clock()
            self._fault = (self._fault[0] if self._fault else now, now)
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
