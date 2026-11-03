"""How fast held asks were answered: ``jarvis-live ask-report [--days N]``.

Reads the ``voice_ask`` rows (docs/plans/voice-push-to-talk.md, Measuring it). Percentiles are
by nearest rank, per device and model, per stage; ``first_audio`` is key released to first
audio on the client's clock, counted only for asks that were played."""

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.db.models import CopilotItem, Device
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.voice.ask_pass import ASK_KIND

STAGES = ("stt", "llm", "first_audio")


@dataclass(frozen=True)
class AskRow:
    device: str
    model: str
    outcome: str
    stages: dict[str, float]
    latency_ms: int | None


@dataclass
class Summary:
    device: str
    model: str
    n: int = 0
    played: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    p50: dict[str, float | None] = field(default_factory=dict)
    p95: dict[str, float | None] = field(default_factory=dict)


def nearest_rank(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def summarize(rows: list[AskRow]) -> list[Summary]:
    groups: dict[tuple[str, str], list[AskRow]] = {}
    for r in rows:
        groups.setdefault((r.device, r.model), []).append(r)
    out: list[Summary] = []
    for (device, model), rs in sorted(groups.items()):
        s = Summary(device, model, n=len(rs))
        for r in rs:
            s.outcomes[r.outcome] = s.outcomes.get(r.outcome, 0) + 1
        values: dict[str, list[float]] = {k: [] for k in STAGES}
        for r in rs:
            for k in ("stt", "llm"):
                if k in r.stages:
                    values[k].append(r.stages[k])
            if r.latency_ms is not None:
                values["first_audio"].append(r.latency_ms / 1000)
        s.played = len(values["first_audio"])
        s.p50 = {k: nearest_rank(v, 0.5) for k, v in values.items()}
        s.p95 = {k: nearest_rank(v, 0.95) for k, v in values.items()}
        out.append(s)
    return out


def _s(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}"


def render(summaries: list[Summary]) -> str:
    head = (
        "| Device | Model | n | Played | "
        + " | ".join(f"{k} p50 / p95, s" for k in STAGES)
        + " | Outcomes |"
    )
    lines = [head, "|" + "---|" * (5 + len(STAGES))]
    for s in summaries:
        cells = " | ".join(f"{_s(s.p50[k])} / {_s(s.p95[k])}" for k in STAGES)
        outcomes = ", ".join(f"{k} {v}" for k, v in sorted(s.outcomes.items()))
        lines.append(f"| {s.device} | {s.model} | {s.n} | {s.played} | {cells} | {outcomes} |")
    return "\n".join(lines)


async def load(sm: "async_sessionmaker[AsyncSession]", *, days: int) -> list[AskRow]:
    since = datetime.now(UTC) - timedelta(days=days)
    async with sm() as db:
        result = await db.execute(
            select(CopilotItem.payload, Device.name)
            .join(SessionRow, SessionRow.id == CopilotItem.session_id)
            .outerjoin(Device, Device.id == SessionRow.device_id)
            .where(CopilotItem.kind == ASK_KIND, CopilotItem.created_at >= since)
            .order_by(CopilotItem.created_at)
        )
        rows: list[tuple[dict[str, Any], str | None]] = [(p, d) for p, d in result.all()]
    return [
        AskRow(
            device or "unknown",
            str(p.get("model", "")),
            str(p.get("outcome", "")),
            {k: float(v) for k, v in (p.get("stages") or {}).items()},
            p.get("latency_ms") if p.get("played") in ("started", "done") else None,
        )
        for p, device in rows
    ]


__all__ = ["AskRow", "Summary", "load", "nearest_rank", "render", "summarize"]
