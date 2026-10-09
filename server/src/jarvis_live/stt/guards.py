"""Post-STT guards: hallucination filter and mic/system duplicate (echo) filter."""

import re
from collections.abc import Iterable
from difflib import SequenceMatcher
from typing import Protocol

import numpy as np

_NON_WORD = re.compile(r"[^\w\s]")


def normalize(text: str) -> str:
    return " ".join(_NON_WORD.sub(" ", text.lower()).split())


def rms_level(pcm: bytes) -> float:
    """RMS of PCM16LE normalized to full scale (0..1)."""
    if not pcm:
        return 0.0
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0
    return float(np.sqrt(np.mean(x * x)))


def is_hallucination(text: str, rms: float, phrases: Iterable[str], rms_threshold: float) -> bool:
    """Whisper emits stock phrases on near-silence. Drop them only when the audio is quiet."""
    if rms >= rms_threshold:
        return False
    norm = normalize(text)
    return any(p and normalize(p) in norm for p in phrases)


class _Span(Protocol):
    start_ms: int
    end_ms: int
    text: str


def overlap_ratio(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    """Overlap as a fraction of the first interval's duration."""
    dur = a_end - a_start
    if dur <= 0:
        return 0.0
    return max(0, min(a_end, b_end) - max(a_start, b_start)) / dur


def is_duplicate_of(
    mic: _Span, system: Iterable[_Span], min_overlap: float, min_similarity: float
) -> bool:
    """True if a system segment overlapping ``mic`` by more than ``min_overlap`` (of the mic
    segment's duration) has text similarity above ``min_similarity`` (speaker echo)."""
    m = normalize(mic.text)
    for s in system:
        if overlap_ratio(mic.start_ms, mic.end_ms, s.start_ms, s.end_ms) <= min_overlap:
            continue
        if SequenceMatcher(None, m, normalize(s.text)).ratio() > min_similarity:
            return True
    return False
