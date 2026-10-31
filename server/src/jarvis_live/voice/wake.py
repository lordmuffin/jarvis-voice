"""Telling a request to Jarvis from talk about Jarvis, and hearing it out to its end. Pure.

"Jarvis Live" and "Jarvis Voice" are names the owner says all the time, so the bare word is not
enough: a request opens with "hey/ok Jarvis", or with "Jarvis" at the start of an utterance or
sentence. What slips through costs one LLM call that answers ``none``."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from jarvis_live.copilot.transcript import Line

_WAKE = re.compile(
    r"(?:\b(?:hey|ok|okay|yo)[,\s]+|^\s*|(?<=[.!?])\s+)"
    r"jarvis\b(?![\s-]*(?:live|voice|app)\b)[\s,.:;!?-]*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Heard:
    """One spoken request: the lines it was said in, and the words after the wake phrase."""

    lines: tuple[Line, ...]
    text: str


def after_wake(text: str) -> str | None:
    """What was said after the wake phrase, or None if ``text`` does not address Jarvis."""
    m = _WAKE.search(text)
    return None if m is None else text[m.end() :].strip()


def next_request(
    lines: Sequence[Line], now_ms: int, settle_ms: int, max_ms: int, *, final: bool = False
) -> tuple[Heard | None, int]:
    """The first finished request in ``lines`` (the speaker's own, oldest first), and how far
    the caller has now read. A request still being spoken is left unread, to be looked at again.
    It ends at ``settle_ms`` of quiet, at the next wake phrase, at ``max_ms`` long, or with the
    session (``final``)."""
    if not lines:
        return None, -1
    start = next((i for i, x in enumerate(lines) if after_wake(x.text) is not None), None)
    if start is None:
        return None, lines[-1].end_ms
    first = lines[start]
    taken = [first]
    ended = final
    for x in lines[start + 1 :]:
        if (
            x.start_ms - taken[-1].end_ms > settle_ms
            or x.end_ms - first.start_ms > max_ms
            or after_wake(x.text) is not None
        ):
            ended = True
            break
        taken.append(x)
    if not ended and now_ms - taken[-1].end_ms < settle_ms:
        return None, first.end_ms - 1
    words = [after_wake(first.text) or "", *(x.text for x in taken[1:])]
    return Heard(tuple(taken), " ".join(w for w in words if w)), taken[-1].end_ms
