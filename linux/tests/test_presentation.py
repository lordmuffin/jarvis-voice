"""What the pill says during hold to ask. Tones are Instrument roles, never colours."""

import pytest

from jarvis_desktop.dictation_text import ELLIPSIS
from jarvis_desktop.presentation import (
    MESSAGE_SECONDS,
    REPLY_MAX_LENGTH,
    REPLY_SECONDS,
    AskPhase,
    AskView,
    NotSpoken,
    PillContent,
    Tone,
    ask_pill,
    key_label,
)
from jarvis_desktop.trigger import TriggerKey

KEY = TriggerKey.RIGHT_ALT


def test_idle_shows_nothing() -> None:
    assert ask_pill(AskView(AskPhase.IDLE), KEY) is None


def test_listening_while_held() -> None:
    assert ask_pill(AskView(AskPhase.LISTENING), KEY) == PillContent(
        "Listening", Tone.LIVE, "Release Right Alt to ask."
    )


def test_waiting_after_release() -> None:
    assert ask_pill(AskView(AskPhase.WAITING), KEY) == PillContent("Thinking", Tone.PROGRESS, None)


def test_reply_text() -> None:
    assert ask_pill(AskView(AskPhase.REPLY, "It's 3 pm."), KEY) == PillContent(
        "Jarvis", Tone.NEUTRAL, "It's 3 pm."
    )


def test_long_reply_keeps_its_start() -> None:
    text = "word " * 100
    shown = ask_pill(AskView(AskPhase.REPLY, text), KEY)
    assert shown is not None and shown.text is not None
    assert len(shown.text) <= REPLY_MAX_LENGTH
    assert shown.text.startswith("word word")
    assert shown.text.endswith("…")


@pytest.mark.parametrize(
    ("phase", "detail", "expected"),
    [
        (
            AskPhase.NO_SESSION,
            "",
            PillContent("Not asked", Tone.NEUTRAL, "Start a capture session to ask Jarvis."),
        ),
        (
            AskPhase.VOICE_OFF,
            "",
            PillContent("Not asked", Tone.NEUTRAL, "Voice is off in this session."),
        ),
        (AskPhase.FAILED, "daemon did not answer in time", None),
        (AskPhase.NOTHING_HEARD, "", PillContent("Didn't catch that", Tone.NEUTRAL, None)),
        (AskPhase.NO_ANSWER, "", PillContent("No answer", Tone.ERROR, "Jarvis didn't reply.")),
    ],
)
def test_messages(phase: AskPhase, detail: str, expected: PillContent | None) -> None:
    shown = ask_pill(AskView(phase, detail), KEY)
    if expected is None:
        assert shown == PillContent("Not asked", Tone.ERROR, detail)
    else:
        assert shown == expected


def test_reply_stays_a_few_seconds_and_messages_less() -> None:
    assert 3 <= REPLY_SECONDS <= 10
    assert MESSAGE_SECONDS < REPLY_SECONDS
    assert AskView(AskPhase.REPLY, "x").dismiss_after == REPLY_SECONDS
    assert AskView(AskPhase.VOICE_OFF).dismiss_after == MESSAGE_SECONDS
    assert AskView(AskPhase.LISTENING).dismiss_after is None
    assert AskView(AskPhase.WAITING).dismiss_after is None


def test_key_labels() -> None:
    assert [key_label(k) for k in TriggerKey if k is not TriggerKey.OFF] == [
        "Right Ctrl",
        "Right Alt",
        "Right Shift",
        "Right Meta",
    ]


def test_tones_are_instrument_roles_not_colours() -> None:
    assert {t.value for t in Tone} == {"neutral", "live", "progress", "error"}


def test_speaking_shows_the_reply_and_stays_up() -> None:
    view = AskView(AskPhase.SPEAKING, "It's 3 pm.")
    assert ask_pill(view, KEY) == PillContent("Speaking", Tone.LIVE, "It's 3 pm.")
    assert view.dismiss_after is None, "until the speech ends"


@pytest.mark.parametrize(
    ("why", "label", "tone", "note"),
    [
        (NotSpoken.ROUTE, "Text only", Tone.NEUTRAL, "Not spoken: no headphones."),
        (
            NotSpoken.TRANSCRIPT,
            "Text only",
            Tone.NEUTRAL,
            "Not spoken: this session records what the computer plays.",
        ),
        (NotSpoken.INTERRUPTED, "Text only", Tone.NEUTRAL, "Stopped: headphones disconnected."),
        (NotSpoken.SPEECH_FAILED, "Not spoken", Tone.ERROR, "Not spoken: speech is unavailable."),
    ],
)
def test_a_reply_not_spoken_says_why(why: NotSpoken, label: str, tone: Tone, note: str) -> None:
    shown = ask_pill(AskView(AskPhase.REPLY, "It's 3 pm.", why), KEY)
    assert shown == PillContent(label, tone, f"It's 3 pm. {note}")


def test_the_note_survives_a_long_reply() -> None:
    shown = ask_pill(AskView(AskPhase.REPLY, "word " * 100, NotSpoken.ROUTE), KEY)
    assert shown is not None and shown.text is not None
    assert shown.text.endswith(f"{ELLIPSIS} Not spoken: no headphones.")
