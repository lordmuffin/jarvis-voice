"""Transcript assembly shared by the copilot loop and the finalizer."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_live.db.models import SegmentRow


@dataclass(frozen=True)
class Line:
    start_ms: int
    end_ms: int
    speaker: str  # "me" | "them"
    text: str


def mmss(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def hhmmss(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def render_lines(lines: list[Line]) -> str:
    return "\n".join(f"[{mmss(x.start_ms)}] {x.speaker}: {x.text}" for x in lines)


def merge_lines(finals: list[Line], drafts: list[Line]) -> list[Line]:
    """Finals, plus any draft whose time range no final covers (STT not finished, or lost)."""
    keep = [
        d
        for d in drafts
        if not any(d.start_ms < f.end_ms and d.end_ms > f.start_ms for f in finals)
    ]
    # Drafts can overlap each other (client re-sends); keep the first of any overlapping run.
    kept: list[Line] = []
    for d in sorted(keep, key=lambda x: x.start_ms):
        if not kept or d.start_ms >= kept[-1].end_ms:
            kept.append(d)
    return sorted([*finals, *kept], key=lambda x: (x.start_ms, x.end_ms))


async def load_lines(db: AsyncSession, session_id: uuid.UUID, from_ms: int = 0) -> list[Line]:
    rows = (
        await db.execute(
            select(SegmentRow)
            .where(SegmentRow.session_id == session_id, SegmentRow.end_ms >= from_ms)
            .order_by(SegmentRow.start_ms)
        )
    ).scalars()
    finals: list[Line] = []
    drafts: list[Line] = []
    for r in rows:
        text = " ".join(r.text.split())
        if not text:
            continue
        (finals if r.source == "final" else drafts).append(
            Line(r.start_ms, r.end_ms, r.speaker, text)
        )
    return merge_lines(finals, drafts)
