"""Hold to ask: the mic audio an ask is cut from (docs/plans/voice-push-to-talk.md).

The client marks the ask with ``ask_begin`` and ``ask_end`` on its audio frames' own clock. The
mic stream carries on as always; this keeps a short pre-roll, and while a key is held, every
frame since. Positions are counted from the audio fed, as the segmenter counts them, so an ask
lines up with the transcript whatever clock offset the client's ``t_ms`` has."""

import re
from collections import deque
from dataclasses import dataclass, field

from jarvis_live.protocol import BYTES_PER_MS

MAX_WINDOWS = 32  # finished asks remembered, so their words are not filed or heard again


@dataclass(frozen=True)
class _Frame:
    t_ms: int  # the client's clock
    pos_ms: int  # stream position, as the segmenter counts it
    pcm: bytes

    @property
    def end_t(self) -> int:
        return self.t_ms + len(self.pcm) // BYTES_PER_MS

    @property
    def end_pos(self) -> int:
        return self.pos_ms + len(self.pcm) // BYTES_PER_MS


@dataclass(frozen=True)
class Clip:
    id: str
    pcm: bytes
    start_ms: int  # stream position
    end_ms: int


@dataclass
class _Held:
    id: str
    begin_t: int
    start_pos: int
    end_t: int | None = None


class AskAudio:
    """Pure and synchronous: ``feed`` runs on the audio path and only appends."""

    def __init__(self, *, preroll_ms: int, max_ms: int) -> None:
        self._preroll = preroll_ms
        self._max = max_ms
        self._frames: deque[_Frame] = deque()
        self._pos = 0
        self._held: _Held | None = None
        self._windows: deque[tuple[int, int]] = deque(maxlen=MAX_WINDOWS)

    def feed(self, t_ms: int, pcm: bytes) -> None:
        f = _Frame(t_ms, self._pos, pcm)
        self._pos = f.end_pos
        self._frames.append(f)
        self._trim()

    def begin(self, ask_id: str, t_ms: int) -> None:
        """A new ask replaces one still held: the client only ever has one."""
        from_t = t_ms - self._preroll
        start = next((f.pos_ms for f in self._frames if f.end_t > from_t), self._pos)
        self._held = _Held(ask_id, t_ms, start)
        self._trim()

    def end(self, ask_id: str, t_ms: int) -> bool:
        held = self._held
        if held is None or held.id != ask_id:
            return False
        held.end_t = min(max(t_ms, held.begin_t), held.begin_t + self._max)
        return True

    def complete(self, ask_id: str) -> bool:
        """The ask has ended and its last frame has arrived (or the hold reached the limit)."""
        held = self._held
        if held is None or held.id != ask_id or not self._frames:
            return False
        last = self._frames[-1].end_t
        if held.end_t is None:
            return last >= held.begin_t + self._max
        return last >= held.end_t

    def take(self, ask_id: str) -> Clip | None:
        """The ask's audio, as far as it has arrived. Frees it."""
        held = self._held
        if held is None or held.id != ask_id:
            return None
        end_t = held.end_t if held.end_t is not None else held.begin_t + self._max
        from_t = held.begin_t - self._preroll
        frames = [f for f in self._frames if f.end_t > from_t and f.t_ms < end_t]
        self._held = None
        end_pos = frames[-1].end_pos if frames else held.start_pos
        self._windows.append((held.start_pos, end_pos))
        self._trim()
        if not frames:
            return None
        return Clip(ask_id, b"".join(f.pcm for f in frames), frames[0].pos_ms, end_pos)

    def drop(self, ask_id: str) -> None:
        """Forget an ask without using its audio (session ending, client gone)."""
        if self._held is not None and self._held.id == ask_id:
            self.take(ask_id)

    def windows(self) -> list[tuple[int, int | None]]:
        """Stream positions of recent asks; a held one has no end yet."""
        out: list[tuple[int, int | None]] = list(self._windows)
        if self._held is not None:
            out.append((self._held.start_pos, None))
        return out

    def buffered_ms(self) -> int:
        return sum(len(f.pcm) for f in self._frames) // BYTES_PER_MS

    def _trim(self) -> None:
        if not self._frames:
            return
        if self._held is not None:
            keep_from = self._held.begin_t - self._preroll
        else:
            keep_from = self._frames[-1].end_t - self._preroll
        while self._frames and self._frames[0].end_t <= keep_from:
            self._frames.popleft()


# --- what is spoken -------------------------------------------------------------------------

_BULLET = re.compile(r"^\s*[-\u2022]\s+", re.MULTILINE)
_MARKUP = re.compile(r"[*_`#>]+")
_SENTENCE_END = re.compile(r"[.!?]$")


def spoken(text: str, max_words: int) -> str:
    """A reply as it should be read aloud: no markup, at most ``max_words``, ending on a
    sentence where one fits."""
    words = _MARKUP.sub("", _BULLET.sub(" ", text)).split()
    if len(words) <= max_words:
        return " ".join(words)
    kept = words[:max_words]
    ends = [i for i, w in enumerate(kept) if _SENTENCE_END.search(w)]
    if ends:
        return " ".join(kept[: ends[-1] + 1])
    return " ".join(kept).rstrip(",;:") + "."


# --- Jarvis heard by a mic ------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9']+")
_NUMBERS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty"
).split()
_ORDINALS = (
    "zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
    "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth"
).split()
# One form for a number however Whisper writes it: "sixth", "6th", "six" and "6" are all "6".
_NUMBER = {w: str(n) for words in (_NUMBERS, _ORDINALS) for n, w in enumerate(words)}
_ORDINAL_DIGITS = re.compile(r"(\d+)(?:st|nd|rd|th)")
_CONTRACTION = {"s": "is", "re": "are", "ll": "will", "ve": "have", "m": "am", "d": "would"}
_NOT = {"ca": "can", "wo": "will", "sha": "shall"}
# How much later than the pad a reply's last words may start after "done", for a reply played on
# a phone speaker: tests/test_voice_ask.py's tail, split off by the VAD, starts 400 ms after done;
# 200 ms past desk.py's 300 ms pad keeps it with 100 ms to spare and closes the window at 500 ms.
# Only a segment that is all the reply's words counts there, so the owner answering is kept.
SPEAKER_TAIL_MS = 200


def _split(word: str) -> list[str]:
    """A contraction as its words: "demo's" is "demo is", "don't" is "do not"."""
    if word.endswith("n't"):
        stem = word[:-3]
        return [_NOT.get(stem, stem), "not"]
    stem, _, rest = word.partition("'")
    return [stem, _CONTRACTION[rest]] if rest in _CONTRACTION else [stem]


def _tokens(text: str) -> set[str]:
    """The words of ``text`` in one form, so Whisper's rewording of the same words matches."""
    words = (w for raw in _WORD.findall(text.lower().replace("\u2019", "'")) for w in _split(raw))
    return {_NUMBER.get(w, _ORDINAL_DIGITS.sub(r"\1", w)) for w in words if w}


@dataclass
class _Played:
    start_ms: int
    end_ms: int | None
    words: set[str] = field(default_factory=set)


class EchoGuard:
    """Drops a segment that is Jarvis's own reply picked up by a mic. Only inside the window the
    client says it played, and only when most of the segment's words are the reply's: speech
    over Jarvis is real, and kept.

    A segment that starts after the reply was done (up to ``pad_ms + tail_ms`` after) is the
    speaker's last words only if every one of its words is the reply's: the owner answering
    reuses the reply's words too, and adds their own."""

    def __init__(self, *, pad_ms: int, threshold: float, tail_ms: int = SPEAKER_TAIL_MS) -> None:
        self._pad = pad_ms
        self._tail = tail_ms
        self._threshold = threshold
        self._played: deque[_Played] = deque(maxlen=MAX_WINDOWS)

    def add(self, start_ms: int, end_ms: int | None, text: str) -> None:
        self._played.append(_Played(start_ms, end_ms, _tokens(text)))

    def close(self, end_ms: int) -> None:
        """The reply still open has finished playing."""
        for p in self._played:
            if p.end_ms is None:
                p.end_ms = max(end_ms, p.start_ms)

    def is_echo(self, start_ms: int, end_ms: int, text: str) -> bool:
        words = _tokens(text)
        if not words:
            return False
        for p in self._played:
            if end_ms <= p.start_ms - self._pad:
                continue
            share = len(words & p.words) / len(words)
            if p.end_ms is None or start_ms < p.end_ms:
                if share >= self._threshold:
                    return True
            elif start_ms < p.end_ms + self._pad + self._tail and share == 1:
                return True
        return False
