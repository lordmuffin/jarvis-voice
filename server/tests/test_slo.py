from typing import Any

from jarvis_live import metrics
from jarvis_live.config import Settings
from jarvis_live.slo import SloMonitor
from tests.fakes import FakeClock, FakeNotifier


class Rig:
    def __init__(self, **cfg: Any) -> None:
        self.clock = FakeClock()
        self.notifier = FakeNotifier()
        self.mon = SloMonitor(Settings(**cfg), self.notifier, self.clock)

    def feed(self, signal: str, key: str, value: float, n: int = 1, gap_s: float = 1.0) -> None:
        for _ in range(n):
            self.mon.observe(signal, key, value)
            self.clock.now += gap_s

    async def check(self, live: bool = True, tier_up: bool = True) -> None:
        await self.mon.check(self.clock.monotonic(), any_live=live, stt_up=tier_up)

    def titles(self) -> list[str]:
        return [s.title for s in self.notifier.sent]


async def test_a_sustained_breach_alerts_exactly_once() -> None:
    r = Rig()
    r.feed("stt_request", "gpu", 9.0, n=10)  # slow tier: p95 9 s against 4 s
    await r.check()
    await r.check()
    r.feed("stt_request", "gpu", 9.0, n=10)
    await r.check()
    assert len(r.notifier.sent) == 1
    sent = r.notifier.sent[0]
    assert "gpu" in sent.title + sent.message and sent.priority == 5


async def test_too_few_samples_is_not_a_sustained_breach() -> None:
    r = Rig()
    r.feed("stt_request", "gpu", 9.0, n=3)
    await r.check()
    assert not r.notifier.sent


async def test_healthy_latency_never_alerts() -> None:
    r = Rig()
    r.feed("stt_request", "gpu", 1.0, n=50)
    r.feed("authoritative", "", 2.0, n=50)
    await r.check()
    assert not r.notifier.sent


async def test_samples_older_than_the_window_do_not_count() -> None:
    r = Rig(slo_window_s=300)
    r.feed("stt_request", "gpu", 9.0, n=10)
    r.clock.now += 400  # the slow burst is now outside the window
    r.feed("stt_request", "gpu", 1.0, n=10)
    await r.check()
    assert not r.notifier.sent


async def test_one_slow_tier_does_not_alert_for_a_healthy_one() -> None:
    r = Rig()
    r.feed("stt_request", "gpu", 9.0, n=10)
    r.feed("stt_request", "cpu", 1.0, n=10)
    await r.check()
    assert len(r.notifier.sent) == 1 and "cpu" not in r.notifier.sent[0].title


async def test_rearms_only_after_a_full_clear_window_then_alerts_again() -> None:
    r = Rig(slo_window_s=300, slo_clear_s=600)
    r.feed("stt_request", "gpu", 9.0, n=10)
    await r.check()
    r.clock.now += 301
    r.feed("stt_request", "gpu", 1.0, n=10)  # recovered, but not for 600 s yet
    await r.check()
    r.feed("stt_request", "gpu", 9.0, n=10)  # slow again before the clear window ran out
    await r.check()
    assert len(r.notifier.sent) == 1
    r.clock.now += 700
    r.feed("stt_request", "gpu", 1.0, n=10)
    await r.check()  # clear: re-armed
    r.feed("stt_request", "gpu", 9.0, n=10)
    await r.check()
    assert len(r.notifier.sent) == 2


async def test_recovery_sends_no_push() -> None:
    r = Rig()
    r.feed("stt_request", "gpu", 9.0, n=10)
    await r.check()
    r.clock.now += 5000
    r.feed("stt_request", "gpu", 1.0, n=10)
    await r.check()
    assert len(r.notifier.sent) == 1


async def test_silent_while_no_session_is_live() -> None:
    r = Rig()
    r.feed("stt_request", "gpu", 9.0, n=10)
    await r.check(live=False)
    assert not r.notifier.sent


async def test_silent_during_an_stt_outage_which_has_its_own_alert() -> None:
    r = Rig()
    r.feed("stt_request", "gpu", 9.0, n=10)
    r.feed("authoritative", "", 20.0, n=10)
    await r.check(tier_up=False)
    assert not r.notifier.sent


async def test_a_throttled_tier_alerts_once_not_again_for_the_lag_it_causes() -> None:
    r = Rig()
    r.feed("stt_request", "gpu", 9.0, n=10)
    r.feed("authoritative", "", 12.0, n=10)  # a consequence of the slow tier
    await r.check()
    r.feed("authoritative", "", 12.0, n=10)
    await r.check()
    assert len(r.notifier.sent) == 1 and "gpu" in r.titles()[0].lower()


async def test_authoritative_alerts_on_its_own_when_no_tier_is_slow() -> None:
    r = Rig()
    r.feed("stt_request", "gpu", 1.0, n=10)
    r.feed("authoritative", "", 12.0, n=10)  # queueing, not STT
    await r.check()
    assert len(r.notifier.sent) == 1


async def test_copilot_staleness_defers_to_a_slow_round_trip() -> None:
    r = Rig()
    r.feed("copilot_roundtrip", "", 20.0, n=10)
    r.feed("copilot_staleness", "", 50.0, n=10)
    await r.check()
    assert len(r.notifier.sent) == 1


async def test_finalize_alerts_when_two_runs_in_a_row_are_slow_but_not_one() -> None:
    r = Rig()
    r.feed("finalize", "", 400.0)
    await r.check(live=False)  # finalizing happens after the session ended
    assert not r.notifier.sent
    r.feed("finalize", "", 400.0)
    await r.check(live=False)
    assert len(r.notifier.sent) == 1


async def test_disabled_monitor_is_silent() -> None:
    r = Rig(slo_enabled=False)
    r.feed("stt_request", "gpu", 9.0, n=10)
    await r.check()
    assert not r.notifier.sent


async def test_breach_state_is_exported_as_a_gauge() -> None:
    r = Rig()
    r.feed("stt_request", "slowtier", 9.0, n=10)
    await r.check()
    assert metrics.sample("jarvis_live_slo_breach", {"slo": "stt_tier", "tier": "slowtier"}) == 1
    r.clock.now += 5000
    r.feed("stt_request", "slowtier", 1.0, n=10)
    await r.check()
    assert metrics.sample("jarvis_live_slo_breach", {"slo": "stt_tier", "tier": "slowtier"}) == 0


async def test_recorded_metrics_reach_the_monitor_through_the_observer_hook() -> None:
    r = Rig()
    metrics.add_observer(r.mon.observe)
    try:
        for _ in range(10):
            metrics.stt_request("hooked", "ok", 9.0)
            r.clock.now += 1
    finally:
        metrics.remove_observer(r.mon.observe)
    await r.check()
    assert len(r.notifier.sent) == 1
