"""Twins of macos/JarvisVoiceMac/Tests/DictationTextTests.swift."""

from jarvis_desktop.dictation_text import Segment, join, tail


def seg(start_ms: int, text: str, final: bool = True) -> Segment:
    return Segment(start_ms=start_ms, end_ms=start_ms + 500, text=text, final=final)


def test_joins_finals_in_time_order() -> None:
    assert join([seg(2_000, "second part."), seg(0, "First part,")]) == "First part, second part."


def test_ignores_volatile_segments() -> None:
    assert join([seg(0, "Kept."), seg(900, "guess", final=False)]) == "Kept."


def test_collapses_whitespace_and_trims() -> None:
    assert join([seg(0, "  Hello \n  there  "), seg(10, "\tfriend ")]) == "Hello there friend"


def test_empty_when_nothing_was_heard() -> None:
    assert join([]) == ""
    assert join([seg(0, "   ")]) == ""


def test_equal_start_times_keep_arrival_order() -> None:
    assert join([seg(0, "one"), seg(0, "two")]) == "one two"


def test_tail_keeps_the_end() -> None:
    assert tail("short") == "short"
    long = "a" * 50 + " " + "b" * 50
    t = tail(long, max_length=20)
    assert len(t) == 20
    assert t.startswith("…")
    assert t.endswith("bbbb")


def test_tail_drops_a_leading_space_after_the_ellipsis() -> None:
    assert tail("x" * 30 + " end", max_length=5) == "…end"
