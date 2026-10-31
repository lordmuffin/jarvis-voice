"""Telling a request to Jarvis from talk about Jarvis, and hearing it out to its end."""

import pytest

from jarvis_live.copilot.transcript import Line
from jarvis_live.voice.wake import after_wake, next_request

SETTLE = 10_000
MAX = 90_000


def line(start_s: float, text: str, seconds: float = 3) -> Line:
    return Line(int(start_s * 1000), int((start_s + seconds) * 1000), "me", text)


@pytest.mark.parametrize(
    ("text", "rest"),
    [
        ("Jarvis, start a Pi session", "start a Pi session"),
        ("jarvis start a Pi session", "start a Pi session"),
        ("Hey Jarvis, remind me at noon", "remind me at noon"),
        ("OK, Jarvis: file a task", "file a task"),
        ("So that was the call. Jarvis, note that down.", "note that down."),
        ("and then hey jarvis open a cloud session", "open a cloud session"),
        ("Jarvis.", ""),
    ],
)
def test_a_request_addressed_to_jarvis_is_heard(text: str, rest: str) -> None:
    assert after_wake(text) == rest


@pytest.mark.parametrize(
    "text",
    [
        # From the session that asked for this feature.
        "The other problem is that I would like to integrate Jarvis Live, the ability",
        "Jarvis Live crashed again during the recording",
        "Jarvis Voice needs a new release",
        "the Jarvis app keeps crashing",
        "I told Jarvis about it",
        "Travis, start a session",
        "",
    ],
)
def test_talk_about_jarvis_is_not_a_request(text: str) -> None:
    assert after_wake(text) is None


def test_no_request_moves_the_cursor_past_what_was_read() -> None:
    heard, cursor = next_request(
        [line(0, "Nothing to see here")], now_ms=60_000, settle_ms=SETTLE, max_ms=MAX
    )
    assert heard is None
    assert cursor == 3000


def test_a_request_waits_until_the_speaker_has_finished() -> None:
    lines = [line(0, "Unrelated."), line(20, "Jarvis, start a Pi session")]
    heard, cursor = next_request(lines, now_ms=25_000, settle_ms=SETTLE, max_ms=MAX)
    assert heard is None
    assert cursor == 22_999  # the wake line is read again next time

    heard, cursor = next_request(lines[1:], now_ms=33_000, settle_ms=SETTLE, max_ms=MAX)
    assert heard is not None
    assert heard.text == "start a Pi session"
    assert cursor == 23_000


def test_a_request_spoken_across_pauses_is_kept_whole() -> None:
    lines = [
        line(0, "Jarvis, start a session"),
        line(6, "um"),
        line(12, "to find out why Flux is failing"),
    ]
    heard, _ = next_request(lines, now_ms=40_000, settle_ms=SETTLE, max_ms=MAX)
    assert heard is not None
    assert heard.text == "start a session um to find out why Flux is failing"
    assert heard.lines == tuple(lines)


def test_speech_after_the_quiet_is_not_part_of_the_request() -> None:
    lines = [line(0, "Jarvis, start a Pi session"), line(30, "Anyway, back to the budget.")]
    heard, cursor = next_request(lines, now_ms=35_000, settle_ms=SETTLE, max_ms=MAX)
    assert heard is not None
    assert heard.text == "start a Pi session"
    assert cursor == 3000  # the budget line is still unread


def test_a_second_request_starts_its_own() -> None:
    lines = [line(0, "Jarvis, add a task"), line(4, "Jarvis, start a Pi session")]
    heard, cursor = next_request(lines, now_ms=35_000, settle_ms=SETTLE, max_ms=MAX)
    assert heard is not None
    assert heard.text == "add a task"
    assert cursor == 3000


def test_a_request_stops_at_the_longest_allowed() -> None:
    lines = [line(i * 5, f"Jarvis, part {i}" if i == 0 else f"part {i}") for i in range(30)]
    heard, _ = next_request(lines, now_ms=150_000, settle_ms=SETTLE, max_ms=MAX)
    assert heard is not None
    assert heard.lines[-1].end_ms - heard.lines[0].start_ms <= MAX


def test_a_long_request_still_speaking_is_handled_at_the_limit() -> None:
    lines = [line(i * 5, "Jarvis, go" if i == 0 else "and on") for i in range(18)]
    heard, _ = next_request(lines, now_ms=lines[-1].end_ms, settle_ms=SETTLE, max_ms=MAX)
    assert heard is None  # still under the limit, still talking
    more = [*lines, line(90, "and on")]
    heard, _ = next_request(more, now_ms=more[-1].end_ms, settle_ms=SETTLE, max_ms=MAX)
    assert heard is not None


def test_the_end_of_the_session_ends_the_request() -> None:
    heard, _ = next_request(
        [line(0, "Jarvis, add a task")], now_ms=4000, settle_ms=SETTLE, max_ms=MAX, final=True
    )
    assert heard is not None
