"""A session's timing, summarised for its final note so a slow session explains itself.

Built from what the live path persisted: ``segments.stt_ms``/``latency_ms``/``stt_tier`` and one
``timing`` copilot item per cycle. Numbers only, judged against the same limits as the SLOs.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_live.config import Settings
from jarvis_live.db.models import CopilotItem, SegmentRow
from jarvis_live.slo import quantile

TIMING_KIND = "timing"
FALLBACK_SHARE = 0.10  # a non-primary tier serving this much of a session is worth saying
FAILED_CYCLES_SHARE = 0.20


@dataclass
class TimingSummary:
    segments: int
    tier_counts: dict[str, int]
    latency_s: list[float] = field(default_factory=list)
    stt_s: dict[str, list[float]] = field(default_factory=dict)
    cycles_ok: int = 0
    cycles_failed: int = 0
    roundtrip_s: list[float] = field(default_factory=list)
    staleness_s: list[float] = field(default_factory=list)


async def load_timing(db: AsyncSession, session_id: uuid.UUID) -> TimingSummary | None:
    """``None`` when nothing was timed (a session from before timing existed, an upload)."""
    rows = (
        await db.execute(
            select(SegmentRow.stt_tier, SegmentRow.stt_ms, SegmentRow.latency_ms).where(
                SegmentRow.session_id == session_id, SegmentRow.source == "final"
            )
        )
    ).all()
    payloads = (
        (
            await db.execute(
                select(CopilotItem.payload)
                .where(CopilotItem.session_id == session_id, CopilotItem.kind == TIMING_KIND)
                .order_by(CopilotItem.id)
            )
        )
        .scalars()
        .all()
    )
    t = TimingSummary(segments=len(rows), tier_counts={})
    stt: dict[str, list[float]] = defaultdict(list)
    for tier, stt_ms, latency_ms in rows:
        if tier:
            t.tier_counts[tier] = t.tier_counts.get(tier, 0) + 1
        if latency_ms is not None:
            t.latency_s.append(latency_ms / 1000)
        if tier and stt_ms is not None:
            stt[tier].append(stt_ms / 1000)
    t.stt_s = dict(stt)
    for p in payloads:
        if p["ok"]:
            t.cycles_ok += 1
            t.roundtrip_s.append(p["rt_s"])
            if p.get("stale_s") is not None:
                t.staleness_s.append(p["stale_s"])
        else:
            t.cycles_failed += 1
    if not (t.latency_s or t.cycles_ok or t.cycles_failed):
        return None
    return t


def _s(x: float) -> str:
    return f"{x:.1f} s"


def _stats(values: list[float]) -> str:
    return f"{_s(quantile(values, 0.5))} / {_s(quantile(values, 0.95))} / {_s(max(values))}"


def render_timing(
    t: TimingSummary,
    cfg: Settings,
    *,
    primary_tier: str | None,
    stop_to_note_s: float | None = None,
) -> list[str]:
    """The note's "Session timing" section, as lines."""
    slow: list[str] = []
    rows: list[tuple[str, str, str]] = []

    mix = ", ".join(
        f"{name} {round(100 * n / t.segments)}%"
        for name, n in sorted(t.tier_counts.items(), key=lambda kv: -kv[1])
    )
    rows.append(("Lines", f"{t.segments}" + (f" ({mix})" if mix else ""), ""))
    if primary_tier and t.segments:
        for name, n in sorted(t.tier_counts.items()):
            if name != primary_tier and n / t.segments >= FALLBACK_SHARE:
                slow.append(
                    f"`{name}` served {round(100 * n / t.segments)}% of lines "
                    f"(primary is `{primary_tier}`)"
                )

    if t.latency_s:
        p95 = quantile(t.latency_s, 0.95)
        rows.append(
            (
                "Line latency p50 / p95 / max",
                _stats(t.latency_s),
                f"p95 ≤ {cfg.slo_authoritative_p95_s:g} s",
            )
        )
        if p95 > cfg.slo_authoritative_p95_s:
            slow.append(f"transcript p95 {_s(p95)} (limit {cfg.slo_authoritative_p95_s:g} s)")
    for name, values in sorted(t.stt_s.items()):
        p95 = quantile(values, 0.95)
        rows.append((f"STT request p95 · {name}", _s(p95), f"≤ {cfg.slo_stt_p95_s:g} s"))
        if p95 > cfg.slo_stt_p95_s:
            slow.append(f"STT tier `{name}` p95 {_s(p95)} (limit {cfg.slo_stt_p95_s:g} s)")

    cycles = t.cycles_ok + t.cycles_failed
    if cycles:
        rows.append(("Copilot cycles", f"{t.cycles_ok} ok, {t.cycles_failed} failed", ""))
        if t.cycles_failed / cycles > FAILED_CYCLES_SHARE:
            slow.append(f"copilot: {t.cycles_failed} of {cycles} cycles failed")
    if t.roundtrip_s:
        p95 = quantile(t.roundtrip_s, 0.95)
        rows.append(
            (
                "Copilot round trip p95 / max",
                f"{_s(p95)} / {_s(max(t.roundtrip_s))}",
                f"p95 ≤ {cfg.slo_copilot_roundtrip_p95_s:g} s",
            )
        )
        if p95 > cfg.slo_copilot_roundtrip_p95_s:
            slow.append(
                f"copilot round trip p95 {_s(p95)} (limit {cfg.slo_copilot_roundtrip_p95_s:g} s)"
            )
    if t.staleness_s:
        p95 = quantile(t.staleness_s, 0.95)
        rows.append(("Notes refresh p95", _s(p95), f"≤ {cfg.slo_copilot_staleness_p95_s:g} s"))
        if p95 > cfg.slo_copilot_staleness_p95_s:
            slow.append(
                f"copilot notes refresh p95 {_s(p95)} (limit {cfg.slo_copilot_staleness_p95_s:g} s)"
            )
    if stop_to_note_s is not None:
        rows.append(
            ("Stop to note rendered", f"{stop_to_note_s:.0f} s", f"≤ {cfg.slo_finalize_s:g} s")
        )
        if stop_to_note_s > cfg.slo_finalize_s:
            slow.append(
                f"note took {stop_to_note_s:.0f} s after stop (limit {cfg.slo_finalize_s:g} s)"
            )

    verdict = "slow — " + "; ".join(slow) + "." if slow else "within budget."
    return [
        "## Session timing",
        "",
        f"**Verdict:** {verdict}",
        "",
        "| Signal | This session | Limit |",
        "|---|---|---|",
        *(f"| {a} | {b} | {c} |" for a, b, c in rows),
        "",
    ]
