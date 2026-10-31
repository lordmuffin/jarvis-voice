"""DONE WHEN: a replayed session produces the full metric set, and a deliberately throttled STT
tier fires exactly one alert. The real app, WebSocket replay, tier pool and scrape port; only
Whisper, the LLM and Gotify are fakes."""

import asyncio
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live import metrics
from jarvis_live.copilot.schema import CopilotDelta, FinalNote, NoteUpsert
from jarvis_live.replay import ReplayProducer
from jarvis_live.stt.backend import WhisperBackend
from jarvis_live.stt.tiers import TierPool, parse_tiers
from tests.audio import TWO_UTTERANCES
from tests.conftest import ServerHandle, _free_port, _serve, _stop
from tests.fakes import FakeLLM, FakeNotifier
from tests.helpers import eventually

SM = async_sessionmaker  # type: ignore[type-arg]
TIER = "e2etier"


class FakeWhisper:
    """An OpenAI-compatible Whisper endpoint whose speed the test controls."""

    def __init__(self) -> None:
        self.delay_s = 0.0
        self.requests = 0
        app = FastAPI()

        @app.get("/v1/models")
        async def models() -> dict[str, list[Any]]:
            return {"data": []}

        @app.post("/v1/audio/transcriptions")
        async def transcribe(request: Request) -> dict[str, str]:
            await request.body()
            self.requests += 1
            await asyncio.sleep(self.delay_s)
            return {"text": f"hello {self.requests}"}

        self.app = app


def v(name: str, **labels: str) -> float:
    return metrics.sample(name, labels) or 0.0


def slo_pushes(notifier: FakeNotifier) -> list[str]:
    return [s.title for s in notifier.sent if "slow" in s.title]


async def test_replay_fills_the_metric_set_and_a_throttled_tier_alerts_once(
    make_server: Callable[..., ServerHandle], token: str, tmp_path: Path
) -> None:
    whisper = FakeWhisper()
    running: list[tuple[uvicorn.Server, threading.Thread]] = []
    whisper_port = _serve(whisper.app, running)
    metrics_port = _free_port()
    notifier = FakeNotifier()
    llm = (
        FakeLLM()
        .queue("CopilotDelta", CopilotDelta(notes_upsert=[NoteUpsert(text="a point")]))
        .queue("FinalNote", FinalNote(title="Standup", summary="Short one."))
    )
    try:
        # The app's own pool and backend, aimed at the fake endpoint. (make_server would
        # otherwise inject its canned FakeSTT, and no tier would ever be asked.)
        pool = TierPool(
            parse_tiers(f"{TIER} http://127.0.0.1:{whisper_port}/v1 test-model"),
            httpx.AsyncClient(),
        )
        server = make_server(
            stt=WhisperBackend(pool),
            llm=llm,
            notifier=notifier,
            outbox_dir=tmp_path / "outbox",
            finalize_recover_max_age_s=0,  # the shared test DB holds other tests' sessions
            metrics_port=metrics_port,
            status_interval_s=0.1,
            copilot_min_segments=1,
            duplicate_hold_ms=500,  # mic lines wait this long for the system channel
            copilot_poll_s=0.1,
            public_base_url="https://j.example",
            watchdog_interval_s=0.2,
            # the budget, scaled down so the throttle is a fraction of a second
            slo_stt_p95_s=0.3,
            slo_min_samples=4,
            slo_window_s=120,
        )
        mic = ReplayProducer(server.url, token, speed=50.0, drafts=True)
        system = ReplayProducer(server.url, token, channel="system", speed=50.0)

        before = {
            name: v(name, **labels)
            for name, labels in {
                "jarvis_live_draft_first_seconds_count": {},
                "jarvis_live_draft_interval_seconds_count": {"channel": "mic"},
                "jarvis_live_draft_lag_seconds_count": {"channel": "mic"},
                "jarvis_live_segment_seconds_count": {"channel": "mic"},
                "jarvis_live_stt_request_seconds_count": {"tier": TIER, "outcome": "ok"},
                "jarvis_live_stt_served_total": {"tier": TIER},
                "jarvis_live_authoritative_seconds_count": {"channel": "mic"},
                "jarvis_live_dup_hold_seconds_count": {},
                "jarvis_live_copilot_roundtrip_seconds_count": {"outcome": "ok"},
                "jarvis_live_copilot_staleness_seconds_count": {},
                "jarvis_live_finalize_seconds_count": {"source": "finalize", "outcome": "ok"},
            }.items()
        }

        # --- 1. a healthy replay: every signal is recorded -----------------------------------
        sid = await system.create_session("e2e", mode="meeting")  # mic + system channels
        await mic.run(TWO_UTTERANCES, session_id=sid, end=False)
        await eventually(
            lambda: (
                v("jarvis_live_copilot_roundtrip_seconds_count", outcome="ok")
                > before["jarvis_live_copilot_roundtrip_seconds_count"]
            )
        )
        async with httpx.AsyncClient(base_url=server.url, headers=mic._auth) as c:
            (await c.post(f"/v1/sessions/{sid}/end")).raise_for_status()
        await eventually(
            lambda: (
                v("jarvis_live_finalize_seconds_count", source="finalize", outcome="ok")
                > before["jarvis_live_finalize_seconds_count"]
            ),
            timeout=20,
        )

        async with httpx.AsyncClient() as c:  # scraped the way Prometheus does
            scrape = (await c.get(f"http://127.0.0.1:{metrics_port}/metrics")).text
        grew = [name for name, was in before.items() if v(name, **_labels_of(name)) > was]
        assert sorted(grew) == sorted(before), set(before) - set(grew)
        for family in (
            "jarvis_live_stt_tier_healthy",
            "jarvis_live_stt_current_tier",
            "jarvis_live_live_sessions",
            "jarvis_live_lag_ms_max",
        ):
            assert family in scrape, family
        assert v("jarvis_live_stt_tier_healthy", tier=TIER) == 1
        assert v("jarvis_live_stt_current_tier", tier=TIER) == 1
        assert not re.search(r"session_id|user_id|title=", scrape)  # no per-session labels
        assert slo_pushes(notifier) == []  # healthy: no alert

        # --- 2. throttle the tier: exactly one alert ------------------------------------------
        whisper.delay_s = 0.6
        for n in range(2):  # 2 segments per replay: four slow requests meet slo_min_samples
            sid = await system.create_session(f"throttled {n}", mode="meeting")
            # The SLO is only judged while a session is live: keep the last one open until the
            # slow responses are in, or the watchdog may never see the window.
            await mic.run(TWO_UTTERANCES, session_id=sid, end=n == 0)
        await eventually(lambda: len(slo_pushes(notifier)) >= 1, timeout=20)
        async with httpx.AsyncClient(base_url=server.url, headers=mic._auth) as c:
            (await c.post(f"/v1/sessions/{sid}/end")).raise_for_status()
        assert slo_pushes(notifier) == [f"Jarvis: STT tier {TIER} slow"]
        assert v("jarvis_live_slo_breach", slo="stt_tier", tier=TIER) == 1

        # --- 3. still throttled, more traffic, more checks: still one alert -------------------
        sid = await system.create_session("still throttled", mode="meeting")
        await mic.run(TWO_UTTERANCES, session_id=sid)
        await asyncio.sleep(1.0)  # several watchdog ticks
        assert slo_pushes(notifier) == [f"Jarvis: STT tier {TIER} slow"]
        # and nothing else paged: the existing outage / failed-note alerts stayed quiet
        titles = [s.title for s in notifier.sent]
        assert not [t for t in titles if "down" in t or "failed" in t], titles
        assert time.monotonic() > 0
    finally:
        _stop(running)


def _labels_of(name: str) -> dict[str, str]:
    if name.endswith(("_seconds_count",)) and "stt_request" in name:
        return {"tier": TIER, "outcome": "ok"}
    if "stt_served" in name:
        return {"tier": TIER}
    if "copilot_roundtrip" in name:
        return {"outcome": "ok"}
    if "finalize" in name:
        return {"source": "finalize", "outcome": "ok"}
    if any(k in name for k in ("segment_seconds", "authoritative", "draft_interval", "draft_lag")):
        return {"channel": "mic"}
    return {}
