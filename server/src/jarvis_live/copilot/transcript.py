"""Transcript assembly shared by the copilot loop and the finalizer."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_live.db.models import SegmentRow, SessionSpeaker


@dataclass(frozen=True)
class Line:
    start_ms: int
    end_ms: int
    speaker: str  # "me" | "them"
    text: str
    segment_id: uuid.UUID | None = None
    # Who, when diarization told voices apart: a name, or "Speaker N". None: just the side.
    speaker_label: str | None = None

    @property
    def who(self) -> str:
        return self.speaker_label or self.speaker


def mmss(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def hhmmss(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def render_lines(lines: list[Line]) -> str:
    return "\n".join(f"[{mmss(x.start_ms)}] {x.who}: {x.text}" for x in lines)


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


async def speaker_labels(db: AsyncSession, session_id: uuid.UUID) -> dict[str, str]:
    """``speaker_id`` -> what to call that voice, for the voices that need a name in the
    transcript. A named voice always has one. An unnamed one is "Speaker N" only where its
    channel holds several voices: with one, the side (``me`` / ``them``) says enough. The owner
    is ``me``, so has none."""
    speakers = (
        (await db.execute(select(SessionSpeaker).where(SessionSpeaker.session_id == session_id)))
        .scalars()
        .all()
    )
    voices: dict[str, int] = {}
    for sp in speakers:
        voices[sp.channel] = voices.get(sp.channel, 0) + 1
    out: dict[str, str] = {}
    for sp in speakers:
        if sp.is_owner:
            continue
        if sp.label:
            out[sp.speaker_id] = sp.label
        elif voices[sp.channel] > 1:
            out[sp.speaker_id] = f"Speaker {sp.ordinal}"
    return out


async def load_lines(db: AsyncSession, session_id: uuid.UUID, from_ms: int = 0) -> list[Line]:
    rows = (
        await db.execute(
            select(SegmentRow)
            .where(SegmentRow.session_id == session_id, SegmentRow.end_ms >= from_ms)
            .order_by(SegmentRow.start_ms)
        )
    ).scalars()
    labels = await speaker_labels(db, session_id)
    finals: list[Line] = []
    drafts: list[Line] = []
    for r in rows:
        text = " ".join(r.text.split())
        if not text:
            continue
        (finals if r.source == "final" else drafts).append(
            Line(r.start_ms, r.end_ms, r.speaker, text, r.id, labels.get(r.speaker_id or ""))
        )
    return merge_lines(finals, drafts)


async def load_final_lines(
    db: AsyncSession, session_id: uuid.UUID, after_ms: int = -1
) -> list[Line]:
    """Final segments only (their ids are stable, unlike drafts'), ending after ``after_ms``."""
    rows = (
        await db.execute(
            select(SegmentRow)
            .where(
                SegmentRow.session_id == session_id,
                SegmentRow.source == "final",
                SegmentRow.end_ms > after_ms,
            )
            .order_by(SegmentRow.start_ms, SegmentRow.created_at)
        )
    ).scalars()
    labels = await speaker_labels(db, session_id)
    out: list[Line] = []
    for r in rows:
        if text := " ".join(r.text.split()):
            out.append(
                Line(r.start_ms, r.end_ms, r.speaker, text, r.id, labels.get(r.speaker_id or ""))
            )
    return out
