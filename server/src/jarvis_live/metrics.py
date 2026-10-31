"""Prometheus metrics for the live path, and the hook the SLO monitor listens on.

Labels come only from small closed sets (``ALLOWED_LABELS``): never a session, user or title.
Recorders are called after the work they measure, never from the audio path, and never raise.
"""

import logging
from collections.abc import Callable, Iterable
from functools import wraps
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server

log = logging.getLogger(__name__)

REGISTRY = CollectorRegistry(auto_describe=True)
ALLOWED_LABELS = frozenset({"channel", "tier", "outcome", "source", "slo"})
_P = "jarvis_live_"

_SHORT = (0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30)
_SEGMENT = (0.5, 1, 2, 3, 5, 8, 12, 15, 20, 30, 60)  # authoritative line: budget edges at 2-15 s
_COPILOT = (1, 2, 4, 8, 15, 25, 35, 45, 60, 90)
_FINALIZE = (5, 10, 30, 60, 90, 120, 180, 300, 600)


def _hist(name: str, doc: str, buckets: Iterable[float], labels: tuple[str, ...] = ()) -> Histogram:
    return Histogram(_P + name, doc, labels, buckets=tuple(buckets), registry=REGISTRY)


_DRAFT_FIRST = _hist(
    "draft_first_seconds", "First audio frame to first partial draft.", _SHORT + (60,)
)
_DRAFT_INTERVAL = _hist(
    "draft_interval_seconds", "Gap between partial drafts.", _SHORT, ("channel",)
)
_DRAFT_LAG = _hist(
    "draft_lag_seconds", "Ingest position minus draft end at arrival.", _SHORT, ("channel",)
)
_SEGMENT_LEN = _hist("segment_seconds", "Speech segment length.", _SEGMENT, ("channel",))
_STT_REQUEST = _hist("stt_request_seconds", "One Whisper request.", _SHORT, ("tier", "outcome"))
_AUTHORITATIVE = _hist(
    "authoritative_seconds", "Segment cut to persisted and published.", _SEGMENT, ("channel",)
)
_DUP_HOLD = _hist("dup_hold_seconds", "Mic segment held for the system channel.", _SEGMENT)
_COPILOT_RT = _hist("copilot_roundtrip_seconds", "Copilot LLM round trip.", _COPILOT, ("outcome",))
_COPILOT_STALE = _hist(
    "copilot_staleness_seconds", "First unseen final to snapshot published.", _COPILOT
)
_FINALIZE_S = _hist(
    "finalize_seconds", "Session end to note written.", _FINALIZE, ("source", "outcome")
)

_STT_SERVED = Counter(
    _P + "stt_served", "Segments transcribed, by tier.", ["tier"], registry=REGISTRY
)
_STT_FALL = Counter(_P + "stt_fallthrough", "Tier tried and failed.", ["tier"], registry=REGISTRY)
_TIER_HEALTHY = Gauge(_P + "stt_tier_healthy", "Probe state.", ["tier"], registry=REGISTRY)
_TIER_CURRENT = Gauge(
    _P + "stt_current_tier", "1 for the tier in use.", ["tier"], registry=REGISTRY
)
_LAG_MAX = Gauge(_P + "lag_ms_max", "Max lag_ms over live sessions.", registry=REGISTRY)
_LIVE = Gauge(_P + "live_sessions", "Sessions streaming now.", registry=REGISTRY)
_SLO_BREACH = Gauge(
    _P + "slo_breach", "1 while an SLO is breached.", ["slo", "tier"], registry=REGISTRY
)

# (signal, key, value): the SLO monitor's windows are fed from here, key is the tier or "".
Observer = Callable[[str, str, float], None]
_observers: list[Observer] = []


def add_observer(fn: Observer) -> None:
    _observers.append(fn)


def remove_observer(fn: Observer) -> None:
    if fn in _observers:
        _observers.remove(fn)


def _safe[**Ps](fn: Callable[Ps, None]) -> Callable[Ps, None]:
    @wraps(fn)
    def run(*args: Ps.args, **kwargs: Ps.kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # metrics must never break the pipeline
            log.warning("metric %s failed", fn.__name__, exc_info=True)

    return run


def _notify(signal: str, key: str, value: float) -> None:
    for fn in list(_observers):
        fn(signal, key, value)


@_safe
def draft_first(seconds: float) -> None:
    _DRAFT_FIRST.observe(seconds)


@_safe
def draft_interval(channel: str, seconds: float) -> None:
    _DRAFT_INTERVAL.labels(channel).observe(seconds)


@_safe
def draft_lag(channel: str, seconds: float) -> None:
    _DRAFT_LAG.labels(channel).observe(max(0.0, seconds))


@_safe
def segment_length(channel: str, seconds: float) -> None:
    _SEGMENT_LEN.labels(channel).observe(seconds)


@_safe
def stt_request(tier: str, outcome: str, seconds: float) -> None:
    _STT_REQUEST.labels(tier, outcome).observe(seconds)
    if outcome == "ok":
        _notify("stt_request", tier, seconds)


@_safe
def stt_served(tier: str) -> None:
    _STT_SERVED.labels(tier).inc()


@_safe
def stt_fallthrough(tier: str) -> None:
    _STT_FALL.labels(tier).inc()


@_safe
def stt_tiers(healthy: dict[str, bool], current: str | None) -> None:
    for name, ok in healthy.items():
        _TIER_HEALTHY.labels(name).set(1 if ok else 0)
        _TIER_CURRENT.labels(name).set(1 if name == current else 0)


@_safe
def authoritative(channel: str, seconds: float, hold_seconds: float = 0.0) -> None:
    """``seconds`` is cut to emitted; the dup-guard hold is excluded from the SLO signal."""
    _AUTHORITATIVE.labels(channel).observe(seconds)
    if hold_seconds > 0:
        _DUP_HOLD.observe(hold_seconds)
    _notify("authoritative", "", max(0.0, seconds - hold_seconds))


@_safe
def copilot_roundtrip(outcome: str, seconds: float) -> None:
    _COPILOT_RT.labels(outcome).observe(seconds)
    if outcome == "ok":
        _notify("copilot_roundtrip", "", seconds)


@_safe
def copilot_staleness(seconds: float) -> None:
    _COPILOT_STALE.observe(seconds)
    _notify("copilot_staleness", "", seconds)


@_safe
def finalize(source: str, outcome: str, seconds: float) -> None:
    _FINALIZE_S.labels(source, outcome).observe(seconds)
    if outcome == "ok":
        _notify("finalize", "", seconds)


@_safe
def sessions(live: int, lag_ms_max: int) -> None:
    _LIVE.set(live)
    _LAG_MAX.set(lag_ms_max)


@_safe
def slo_breach(slo: str, tier: str, breached: bool) -> None:
    _SLO_BREACH.labels(slo, tier).set(1 if breached else 0)


def sample(name: str, labels: dict[str, str] | None = None) -> Any:
    """Current value of a sample (tests and the replay check)."""
    return REGISTRY.get_sample_value(name, labels or {})


class MetricsServer:
    def __init__(self, port: int) -> None:
        self._httpd, self._thread = start_http_server(port, registry=REGISTRY)
        self.port: int = self._httpd.server_port

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def serve(port: int) -> MetricsServer:
    """Serve ``/metrics`` on its own port, in a daemon thread. The app port is routed to the
    internet by each channel's Ingress; this one is exposed only on the Service, for Prometheus."""
    return MetricsServer(port)
