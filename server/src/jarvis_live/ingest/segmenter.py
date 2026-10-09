"""Per-channel VAD segmentation of a continuous PCM16 16 kHz mono stream."""

from collections import deque
from dataclasses import dataclass

import webrtcvad

SAMPLE_RATE = 16_000
BYTES_PER_MS = 32


@dataclass(frozen=True)
class SegmenterConfig:
    aggressiveness: int = 2
    frame_ms: int = 30
    close_silence_ms: int = 700
    max_ms: int = 15_000
    overlap_ms: int = 500
    min_speech_ms: int = 400


@dataclass(frozen=True)
class SpeechSegment:
    start_ms: int
    end_ms: int
    pcm: bytes
    speech_ms: int


@dataclass
class _Frame:
    pcm: bytes
    speech: bool


class Segmenter:
    """Feed arbitrary-sized PCM chunks; get closed segments back.

    Times are derived from the sample count, i.e. they assume a gap-free stream (the ingest
    layer guarantees contiguous sequence numbers). A segment ends at its last speech frame;
    trailing silence that triggered the close is not included.
    """

    def __init__(self, cfg: SegmenterConfig | None = None) -> None:
        self.cfg = cfg or SegmenterConfig()
        self._vad = webrtcvad.Vad(self.cfg.aggressiveness)
        self._frame_bytes = self.cfg.frame_ms * BYTES_PER_MS
        self._buf = b""
        self._frames_seen = 0
        self._open: deque[_Frame] = deque()
        self._open_start_frame = 0
        self._carry = 0  # leading frames of the open segment re-used from the previous one
        self._close_frames = -(-self.cfg.close_silence_ms // self.cfg.frame_ms)
        self._max_frames = self.cfg.max_ms // self.cfg.frame_ms
        self._overlap_frames = -(-self.cfg.overlap_ms // self.cfg.frame_ms)

    @property
    def position_ms(self) -> int:
        """Stream time of everything consumed in whole frames."""
        return self._frames_seen * self.cfg.frame_ms

    @property
    def open_start_ms(self) -> int | None:
        """Start of the in-progress segment, if any."""
        return self._open_start_frame * self.cfg.frame_ms if self._open else None

    def feed(self, pcm: bytes) -> list[SpeechSegment]:
        self._buf += pcm
        out: list[SpeechSegment] = []
        n = self._frame_bytes
        while len(self._buf) >= n:
            frame, self._buf = self._buf[:n], self._buf[n:]
            out.extend(self._push(frame))
        return out

    def flush(self) -> list[SpeechSegment]:
        """Close the in-progress segment (end of stream). A partial trailing frame is dropped."""
        self._buf = b""
        seg = self._close(self._frames_seen - self._trailing_silence())
        return [seg] if seg else []

    # ------------------------------------------------------------------------------------

    def _push(self, pcm: bytes) -> list[SpeechSegment]:
        speech = self._vad.is_speech(pcm, SAMPLE_RATE)
        idx = self._frames_seen
        self._frames_seen += 1
        if not self._open:
            if not speech:
                return []
            self._open_start_frame = idx
        self._open.append(_Frame(pcm, speech))

        out: list[SpeechSegment] = []
        if self._trailing_silence() >= self._close_frames:
            seg = self._close(self._frames_seen - self._trailing_silence())
            if seg:
                out.append(seg)
        elif len(self._open) >= self._max_frames:
            seg = self._build(self._frames_seen)
            if seg:
                out.append(seg)
            carry = list(self._open)[-self._overlap_frames :]
            self._open = deque(carry)
            self._open_start_frame = self._frames_seen - len(carry)
            self._carry = len(carry)
        return out

    def _trailing_silence(self) -> int:
        n = 0
        for f in reversed(self._open):
            if f.speech:
                break
            n += 1
        return n

    def _close(self, end_frame: int) -> SpeechSegment | None:
        seg = self._build(end_frame)
        self._open = deque()
        self._carry = 0
        return seg

    def _build(self, end_frame: int) -> SpeechSegment | None:
        """Segment covering the open frames up to ``end_frame`` (exclusive); None if too short."""
        frames = list(self._open)[: end_frame - self._open_start_frame]
        speech_ms = sum(f.speech for f in frames) * self.cfg.frame_ms
        # Speech that is only the carried-over overlap was already emitted with the last segment.
        fresh_ms = sum(f.speech for f in frames[self._carry :]) * self.cfg.frame_ms
        if fresh_ms < self.cfg.min_speech_ms:
            return None
        return SpeechSegment(
            start_ms=self._open_start_frame * self.cfg.frame_ms,
            end_ms=end_frame * self.cfg.frame_ms,
            pcm=b"".join(f.pcm for f in frames),
            speech_ms=speech_ms,
        )
