"""Per-session processing: VAD segmentation -> STT -> guards -> persist -> publish."""

import asyncio
import math
import time
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.db.models import SegmentRow
from jarvis_live.ingest.segmenter import Segmenter, SegmenterConfig, SpeechSegment
from jarvis_live.ingest.store import SPEAKER
from jarvis_live.logging_setup import session_logger
from jarvis_live.protocol import Error, Segment, Status
from jarvis_live.stt.backend import STTBackend, STTUnavailable
from jarvis_live.stt.guards import is_duplicate_of, is_hallucination, rms_level


@dataclass
class _Final:
    channel: str
    start_ms: int
    end_ms: int
    text: str
    tier: str


class SessionPipeline:
    def __init__(
        self,
        session_id: uuid.UUID,
        channels: list[str],
        *,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        bus: Bus,
        stt: STTBackend,
    ) -> None:
        self.session_id = session_id
        self.channels = channels
        self._cfg = settings
        self._sm = sessionmaker
        self._bus = bus
        self._stt = stt
        self._log = session_logger(__name__, session_id)
        seg_cfg = SegmenterConfig(
            aggressiveness=settings.vad_aggressiveness,
            frame_ms=settings.vad_frame_ms,
            close_silence_ms=settings.segment_close_silence_ms,
            max_ms=settings.segment_max_ms,
            overlap_ms=settings.segment_overlap_ms,
            min_speech_ms=settings.segment_min_speech_ms,
        )
        self._segmenters = {c: Segmenter(seg_cfg) for c in channels}
        self._queues: dict[str, asyncio.Queue[SpeechSegment]] = {
            c: asyncio.Queue() for c in channels
        }
        self._inflight: dict[str, set[int]] = {c: set() for c in channels}  # segment start_ms
        self._prev_text: dict[str, str] = {c: "" for c in channels}
        self._system_finals: list[_Final] = []
        self._held_mic: list[_Final] = []
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._finishing = False
        self._last_final_end_ms = 0
        self.llm_ok = False  # set by the copilot loop; False until an LLM cycle has succeeded
        self.last_audio_at = time.monotonic()
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._worker(c)) for c in self.channels]
        self._tasks.append(asyncio.create_task(self._releaser()))
        self._tasks.append(asyncio.create_task(self._status_loop()))

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    # --- input ------------------------------------------------------------------------------

    def feed(self, channel: str, pcm: bytes) -> None:
        if self._finishing:
            return
        self.last_audio_at = time.monotonic()
        for seg in self._segmenters[channel].feed(pcm):
            self._enqueue(channel, seg)
        if channel == "system":
            self._wake.set()  # system position advanced: held mic segments may be releasable

    async def finish(self) -> None:
        """End of stream: flush open segments, transcribe everything, release held segments."""
        self._finishing = True
        for channel, segmenter in self._segmenters.items():
            for seg in segmenter.flush():
                self._enqueue(channel, seg)
        for q in self._queues.values():
            await q.join()
        async with self._lock:
            await self._release_held()

    def _enqueue(self, channel: str, seg: SpeechSegment) -> None:
        self._inflight[channel].add(seg.start_ms)
        self._queues[channel].put_nowait(seg)

    # --- STT --------------------------------------------------------------------------------

    async def _worker(self, channel: str) -> None:
        queue = self._queues[channel]
        while True:
            seg = await queue.get()
            try:
                await self._process(channel, seg)
            except Exception:
                self._log.exception("segment processing failed", extra={"channel": channel})
            finally:
                async with self._lock:
                    self._inflight[channel].discard(seg.start_ms)
                queue.task_done()
                self._wake.set()

    async def _process(self, channel: str, seg: SpeechSegment) -> None:
        prompt = self._prev_text[channel][-self._cfg.stt_prompt_chars :]
        try:
            result = await self._stt.transcribe(seg.pcm, prompt)
        except STTUnavailable:
            self._log.error("no STT tier available, segment lost", extra={"channel": channel})
            self._bus.publish(
                str(self.session_id),
                Error(code="stt_unavailable", message="no STT tier could transcribe a segment"),
            )
            return
        text = result.text.strip()
        if not text:
            return
        if is_hallucination(
            text,
            rms_level(seg.pcm),
            self._cfg.hallucination_phrases,
            self._cfg.hallucination_rms_threshold,
        ):
            self._log.info("dropped hallucination", extra={"channel": channel, "text": text})
            return
        self._prev_text[channel] = text
        final = _Final(channel, seg.start_ms, seg.end_ms, text, result.tier)
        async with self._lock:
            if channel == "system" or "system" not in self.channels:
                await self._emit(final)
            else:
                self._held_mic.append(final)
            await self._release_held()

    # --- duplicate guard: mic segments wait until the system channel has settled past them ----

    def _system_settled_ms(self) -> float:
        if "system" not in self.channels:
            return math.inf
        seg = self._segmenters["system"]
        open_start = seg.open_start_ms
        horizon = seg.position_ms if open_start is None else open_start
        return min([horizon, *self._inflight["system"]])

    async def _release_held(self) -> None:
        settled = self._system_settled_ms()
        mic_pos = self._segmenters["mic"].position_ms
        keep: list[_Final] = []
        for m in self._held_mic:
            ready = (
                self._finishing
                or settled >= m.end_ms
                or mic_pos - m.end_ms >= self._cfg.duplicate_hold_ms
            )
            if not ready:
                keep.append(m)
            elif is_duplicate_of(
                m,
                self._system_finals,
                self._cfg.duplicate_overlap_ratio,
                self._cfg.duplicate_similarity,
            ):
                self._log.info("dropped mic duplicate of system", extra={"text": m.text})
            else:
                await self._emit(m)
        self._held_mic = keep

    async def _releaser(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            async with self._lock:
                await self._release_held()

    # --- persist, then publish --------------------------------------------------------------

    async def _emit(self, f: _Final) -> None:
        seg_id = uuid.uuid4()
        async with self._sm() as db:
            db.add(
                SegmentRow(
                    id=seg_id,
                    session_id=self.session_id,
                    channel=f.channel,
                    speaker=SPEAKER[f.channel],
                    start_ms=f.start_ms,
                    end_ms=f.end_ms,
                    text=f.text,
                    source="final",
                    stt_tier=f.tier,
                )
            )
            await db.commit()
        if f.channel == "system":
            self._system_finals.append(f)
        self._last_final_end_ms = max(self._last_final_end_ms, f.end_ms)
        self._bus.publish(
            str(self.session_id),
            Segment(
                id=str(seg_id),
                channel="system" if f.channel == "system" else "mic",
                speaker="them" if f.channel == "system" else "me",
                start_ms=f.start_ms,
                end_ms=f.end_ms,
                text=f.text,
                stt_tier=f.tier,
            ),
        )

    # --- status -----------------------------------------------------------------------------

    def position_ms(self) -> int:
        """Stream time ingested so far (the later of the channels)."""
        return max(s.position_ms for s in self._segmenters.values())

    def publish_status(self) -> None:
        self._bus.publish(
            str(self.session_id),
            Status(stt_tier=self._stt.current_tier(), llm_ok=self.llm_ok, lag_ms=self.lag_ms()),
        )

    def lag_ms(self) -> int:
        """Ingested stream time minus the end of the last finalized segment."""
        position = max(s.position_ms for s in self._segmenters.values())
        return max(0, position - self._last_final_end_ms) if self._last_final_end_ms else 0

    async def _status_loop(self) -> None:
        while True:
            await asyncio.sleep(self._cfg.status_interval_s)
            self.publish_status()
