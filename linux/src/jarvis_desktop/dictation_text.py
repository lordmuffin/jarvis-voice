"""The text a dictation copies and pastes, built from its final segments. Pure.

A port of macos/JarvisVoiceMac/Sources/Dictation/DictationText.swift. ``Segment`` is local on
purpose: dictation never builds protocol messages, so it has nothing that could be sent."""

from dataclasses import dataclass

ELLIPSIS = "…"


@dataclass(frozen=True)
class Segment:
    start_ms: int
    end_ms: int
    text: str
    final: bool


def join(segments: list[Segment]) -> str:
    """Final segments in time order, joined by single spaces, trimmed."""
    # sorted() is stable, so equal start times keep their arrival order.
    finals = sorted((s for s in segments if s.final), key=lambda s: s.start_ms)
    return " ".join(word for s in finals for word in s.text.split())


def tail(text: str, max_length: int = 80) -> str:
    """The end of the live text that fits in the dictation pill."""
    if len(text) <= max_length:
        return text
    return ELLIPSIS + text[-(max_length - 1) :].lstrip(" ")
