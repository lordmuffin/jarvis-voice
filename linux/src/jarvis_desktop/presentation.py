"""What the pill says. Pure: no Qt.

Hold to ask, after the Mac's ``AskPresentation`` (macos/JarvisVoiceMac/Sources/UI/
PresentationLogic.swift): listening while the key is held, thinking after release, speaking
while the reply plays, then its text for a few seconds, saying why if it was not spoken.
``Tone`` names an Instrument role (``InstPill``'s tone), so the QML picks the colour from
Instrument's tokens and nothing here is a colour, size or font."""

from dataclasses import dataclass
from enum import Enum

from jarvis_desktop.dictation_text import ELLIPSIS
from jarvis_desktop.trigger import TriggerKey

#: How long a reply stays in the pill.
REPLY_SECONDS = 6.0
#: How long a message (not asked, nothing heard) stays: the dictation pill's dismiss.
MESSAGE_SECONDS = 2.5
#: Replies are short by design (one LLM call, a sentence or two); a longer one is cut.
REPLY_MAX_LENGTH = 200

_KEY_LABELS = {
    TriggerKey.RIGHT_CTRL: "Right Ctrl",
    TriggerKey.RIGHT_ALT: "Right Alt",
    TriggerKey.RIGHT_SHIFT: "Right Shift",
    TriggerKey.RIGHT_META: "Right Meta",
}


class Tone(Enum):
    NEUTRAL = "neutral"
    LIVE = "live"
    PROGRESS = "progress"
    ERROR = "error"


class AskPhase(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    WAITING = "waiting"
    SPEAKING = "speaking"
    REPLY = "reply"
    NOTHING_HEARD = "nothing_heard"
    NO_ANSWER = "no_answer"
    NO_SESSION = "no_session"
    VOICE_OFF = "voice_off"
    FAILED = "failed"


class NotSpoken(Enum):
    """Why a reply that could have been spoken is shown as text only."""

    ROUTE = "route"  # the default sink is not headphones
    TRANSCRIPT = "transcript"  # the session records what this box plays (--system)
    INTERRUPTED = "interrupted"  # the headphones went while speaking
    SPEECH_FAILED = "speech_failed"


_NOTES = {
    NotSpoken.ROUTE: "Not spoken: no headphones.",
    NotSpoken.TRANSCRIPT: "Not spoken: this session records what the computer plays.",
    NotSpoken.INTERRUPTED: "Stopped: headphones disconnected.",
    NotSpoken.SPEECH_FAILED: "Not spoken: speech is unavailable.",
}


@dataclass(frozen=True)
class AskView:
    phase: AskPhase
    #: The reply for ``SPEAKING`` and ``REPLY``, the error for ``FAILED``.
    text: str = ""
    #: For ``REPLY``: why it was not spoken; None if it was, or was never meant to be.
    not_spoken: NotSpoken | None = None

    @property
    def dismiss_after(self) -> float | None:
        """Seconds until the pill hides by itself; None while the ask is still going."""
        if self.phase in (AskPhase.IDLE, AskPhase.LISTENING, AskPhase.WAITING, AskPhase.SPEAKING):
            return None
        return REPLY_SECONDS if self.phase is AskPhase.REPLY else MESSAGE_SECONDS


@dataclass(frozen=True)
class PillContent:
    label: str
    tone: Tone
    text: str | None


def key_label(key: TriggerKey) -> str:
    return _KEY_LABELS.get(key, "the ask key")


def ask_pill(view: AskView, key: TriggerKey) -> PillContent | None:
    match view.phase:
        case AskPhase.IDLE:
            return None
        case AskPhase.LISTENING:
            return PillContent("Listening", Tone.LIVE, f"Release {key_label(key)} to ask.")
        case AskPhase.WAITING:
            return PillContent("Thinking", Tone.PROGRESS, None)
        case AskPhase.SPEAKING:
            return PillContent("Speaking", Tone.LIVE, _head(view.text.strip()))
        case AskPhase.REPLY:
            text = _head(view.text.strip())
            if view.not_spoken is None:
                return PillContent("Jarvis", Tone.NEUTRAL, text)
            note = f"{text} {_NOTES[view.not_spoken]}"
            if view.not_spoken is NotSpoken.SPEECH_FAILED:
                return PillContent("Not spoken", Tone.ERROR, note)
            return PillContent("Text only", Tone.NEUTRAL, note)
        case AskPhase.NOTHING_HEARD:
            return PillContent("Didn't catch that", Tone.NEUTRAL, None)
        case AskPhase.NO_ANSWER:
            return PillContent("No answer", Tone.ERROR, "Jarvis didn't reply.")
        case AskPhase.NO_SESSION:
            return PillContent("Not asked", Tone.NEUTRAL, "Start a capture session to ask Jarvis.")
        case AskPhase.VOICE_OFF:
            return PillContent("Not asked", Tone.NEUTRAL, "Voice is off in this session.")
        case AskPhase.FAILED:
            return PillContent("Not asked", Tone.ERROR, view.text)


def _head(text: str) -> str:
    if len(text) <= REPLY_MAX_LENGTH:
        return text
    return text[: REPLY_MAX_LENGTH - 1].rstrip(" ") + ELLIPSIS
