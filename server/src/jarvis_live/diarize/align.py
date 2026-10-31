"""Which diarized speaker spoke a transcript segment."""

from collections import defaultdict
from dataclasses import dataclass

MIN_COVERAGE = 0.3  # of the segment, inside any turn, to name a speaker at all
MIXED_RATIO = 0.25  # of the segment a second speaker must cover to mark it mixed


@dataclass(frozen=True)
class Turn:
    start_ms: int
    end_ms: int
    speaker: str


def assign(
    start_ms: int,
    end_ms: int,
    turns: list[Turn],
    *,
    mixed_ratio: float = MIXED_RATIO,
    min_coverage: float = MIN_COVERAGE,
) -> tuple[str | None, bool]:
    """``(speaker, mixed)`` for a segment: the speaker whose turns overlap it most, and whether
    another speaker covers more than ``mixed_ratio`` of it. Segments are VAD-cut, not
    speaker-cut, so one can span a change of speaker; it keeps the majority speaker.
    ``None`` when too little of the segment lies inside any turn."""
    duration = end_ms - start_ms
    if duration <= 0:
        return None, False
    overlap: dict[str, int] = defaultdict(int)
    for t in turns:
        covered = min(end_ms, t.end_ms) - max(start_ms, t.start_ms)
        if covered > 0:
            overlap[t.speaker] += covered
    if not overlap:
        return None, False
    ranked = sorted(overlap.items(), key=lambda kv: (-kv[1], kv[0]))
    top, top_ms = ranked[0]
    if top_ms / duration < min_coverage:
        return None, False
    mixed = len(ranked) > 1 and ranked[1][1] / duration > mixed_ratio
    return top, mixed
