"""Service-level objectives for the live path, checked from the watchdog.

Each signal keeps a short window of recent values (fed from ``metrics``, so the same numbers the
dashboard shows). A sustained breach sends one Gotify push, then stays quiet until the signal
has been healthy for a full clear window. There is no recovery push: the ``slo_breach`` gauge
carries the state.

An effect never repeats its cause: while a tier is breached the authoritative-line SLO is
tracked but silent, and copilot staleness (which includes the round trip) defers to the round
trip. All-tiers-down and finalizer-failed have their own alerts and are not repeated here.
"""

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Protocol

from jarvis_live import metrics
from jarvis_live.config import Settings
from jarvis_live.notify.gotify import Notifier


class _Clock(Protocol):
    def monotonic(self) -> float: ...


@dataclass(frozen=True)
class _Spec:
    slo: str  # the ``slo`` label on the breach gauge
    signal: str
    threshold_s: float
    quantile: float
    window_s: float
    min_samples: int
    live_only: bool  # only judged while a session is live and an STT tier is up
    defers_to: str | None  # an slo whose breach already explains this one
    label: str


@dataclass
class _State:
    breached: bool = False
    last_bad: float = float("-inf")


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]  # nearest rank


class SloMonitor:
    def __init__(self, settings: Settings, notifier: Notifier, clock: _Clock) -> None:
        self._cfg = settings
        self._notifier = notifier
        self._clock = clock
        c = settings

        def p95(slo: str, limit: float, label: str, defers_to: str | None = None) -> _Spec:
            return _Spec(
                slo, "stt_request" if slo == "stt_tier" else slo, limit, 0.95,
                c.slo_window_s, c.slo_min_samples, True, defers_to, label,
            )  # fmt: skip

        self._specs = [  # causes before the effects that defer to them
            p95("stt_tier", c.slo_stt_p95_s, "STT tier"),
            p95("authoritative", c.slo_authoritative_p95_s, "Transcript", "stt_tier"),
            p95("copilot_roundtrip", c.slo_copilot_roundtrip_p95_s, "Copilot LLM"),
            p95(
                "copilot_staleness",
                c.slo_copilot_staleness_p95_s,
                "Copilot notes",
                "copilot_roundtrip",
            ),
            # Rare events: the last runs over six hours. The median of at least two means two
            # slow notes in a row.
            _Spec(
                "finalize",
                "finalize",
                c.slo_finalize_s,
                0.5,
                6 * 3600.0,
                2,
                False,
                None,
                "Final note",
            ),
        ]
        self._windows: dict[tuple[str, str], deque[tuple[float, float]]] = defaultdict(
            lambda: deque(maxlen=4096)
        )
        self._state: dict[tuple[str, str], _State] = defaultdict(_State)

    def observe(self, signal: str, key: str, value: float) -> None:
        """Called from the metric recorders: a deque append, nothing more."""
        if self._cfg.slo_enabled:
            self._windows[(signal, key)].append((self._clock.monotonic(), value))

    async def check(self, now: float, *, any_live: bool, stt_up: bool) -> None:
        if not self._cfg.slo_enabled:
            return
        for spec in self._specs:
            if spec.live_only and not (any_live and stt_up):
                continue  # nothing to judge, or the outage alert owns it
            for (signal, key), window in list(self._windows.items()):
                if signal == spec.signal:
                    await self._judge(spec, key, window, now)

    async def _judge(
        self, spec: _Spec, key: str, window: deque[tuple[float, float]], now: float
    ) -> None:
        while window and window[0][0] < now - spec.window_s:
            window.popleft()
        values = [v for _, v in window]
        bad = (
            len(values) >= spec.min_samples and _quantile(values, spec.quantile) > spec.threshold_s
        )
        state = self._state[(spec.slo, key)]
        if bad:
            state.last_bad = now
            if not state.breached:
                state.breached = True
                if not self._explained(spec):
                    await self._alert(spec, key, _quantile(values, spec.quantile), len(values))
        elif state.breached and now - state.last_bad >= self._cfg.slo_clear_s:
            state.breached = False
        metrics.slo_breach(spec.slo, key, state.breached)

    def _explained(self, spec: _Spec) -> bool:
        return spec.defers_to is not None and any(
            st.breached for (slo, _), st in self._state.items() if slo == spec.defers_to
        )

    async def _alert(self, spec: _Spec, key: str, value: float, samples: int) -> None:
        what = f"{spec.label} {key}".strip()
        stat = "p95" if spec.quantile == 0.95 else "median"
        span = (
            "the last runs"
            if spec.slo == "finalize"
            else f"the last {int(spec.window_s // 60)} min"
        )
        await self._notifier.notify(
            f"Jarvis: {what} slow",
            f"{stat} {value:.1f}s over {span} (limit {spec.threshold_s:g}s, {samples} samples).",
            5,
        )
