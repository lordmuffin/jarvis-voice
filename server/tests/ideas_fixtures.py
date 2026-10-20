"""Fixture transcripts for the ideas pass, in the copilot's ``[mm:ss] me|them: text`` format
(``tests/fixtures/ideas/*.txt``). Each line becomes a final segment that lasts until the next."""

import uuid
from pathlib import Path

from jarvis_live.copilot.transcript import Line

FIXTURES = Path(__file__).parent / "fixtures" / "ideas"


def parse(name: str) -> list[tuple[int, int, str, str]]:
    """``(start_ms, end_ms, speaker, text)`` rows, as ``fakes.add_segments`` takes them."""
    rows: list[tuple[int, str, str]] = []
    for raw in (FIXTURES / name).read_text().splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        stamp, rest = raw[1:].split("] ", 1)
        speaker, text = rest.split(": ", 1)
        mm, ss = stamp.split(":")
        rows.append(((int(mm) * 60 + int(ss)) * 1000, speaker, text))
    out = []
    for i, (start, speaker, text) in enumerate(rows):
        end = rows[i + 1][0] - 100 if i + 1 < len(rows) else start + 5000
        out.append((start, min(end, start + 15_000), speaker, text))
    return out


def lines(name: str) -> list[Line]:
    """The fixture as transcript lines with (fresh) segment ids."""
    return [Line(s, e, sp, t, uuid.uuid4()) for s, e, sp, t in parse(name)]
