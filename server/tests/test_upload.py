"""Uploading a recorded file: decoded to the stream codec, then ingested like a live session."""

import asyncio
import io
import uuid
import wave
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.copilot.schema import FinalNote
from jarvis_live.db.models import OWNER_USER_ID
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.ingest.pipeline import SessionPipeline
from jarvis_live.ingest.upload import (
    FRAME_MS,
    UPLOAD_MAX_BACKLOG,
    UndecodableAudio,
    Uploads,
    decode_to_pcm,
)
from jarvis_live.protocol import BYTES_PER_MS, MAX_PAYLOAD
from jarvis_live.stt.backend import Transcription
from tests.audio import SR, TWO_UTTERANCES, dominant_freq, silence
from tests.conftest import ServerHandle
from tests.fakes import FakeLLM
from tests.helpers import eventually
from tests.work_item_helpers import device_client, web_client

SM = async_sessionmaker  # type: ignore[type-arg]


def wav_bytes(pcm: bytes, *, rate: int = SR, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def stereo_tone_44k(seconds: float, freq: float = 440.0) -> bytes:
    t = np.arange(int(44_100 * seconds)) / 44_100
    mono = (np.sin(2 * np.pi * freq * t) * 0.5 * 32767).astype("<i2")
    return np.repeat(mono, 2).tobytes()  # interleaved L/R


# --- decoding ---------------------------------------------------------------------------------


async def test_decode_resamples_any_wav_to_16k_mono_pcm16(tmp_path: Path) -> None:
    src = tmp_path / "in.wav"
    src.write_bytes(wav_bytes(stereo_tone_44k(2.0), rate=44_100, channels=2))
    dst = tmp_path / "out.pcm"

    await decode_to_pcm(src, dst)

    pcm = dst.read_bytes()
    assert abs(len(pcm) - 2 * SR * 2) <= 2 * 160  # 2 s of 16 kHz 16-bit mono, give or take 10 ms
    assert abs(dominant_freq(pcm) - 440) <= 5


async def test_decode_rejects_a_file_that_is_not_audio(tmp_path: Path) -> None:
    src = tmp_path / "notes.txt"
    src.write_text("this is not a recording\n" * 50)

    with pytest.raises(UndecodableAudio):
        await decode_to_pcm(src, tmp_path / "out.pcm")


# --- the endpoint -----------------------------------------------------------------------------


async def _wait_for_status(c: httpx.AsyncClient, sid: str, status: str) -> dict:  # type: ignore[type-arg]
    for _ in range(400):
        body = (await c.get(f"/v1/sessions/{sid}")).json()
        if body["status"] == status:
            return body  # type: ignore[no-any-return]
        await asyncio.sleep(0.05)
    raise AssertionError(f"session never reached {status}: {body['status']}")


async def test_uploaded_recording_is_transcribed_and_gets_a_note(
    make_server: Callable[..., ServerHandle], token: str, tmp_path: Path
) -> None:
    outbox = tmp_path / "outbox"
    server = make_server(
        llm=FakeLLM().queue("FinalNote", FinalNote(title="Voice memo", summary="Two thoughts.")),
        outbox_dir=outbox,
        finalize_recover_max_age_s=0,
    )
    async with device_client(server, token) as c:
        r = await c.post(
            "/v1/sessions/upload",
            params={"filename": "memo 2026-10-12.wav", "mode": "solo"},
            content=wav_bytes(TWO_UTTERANCES),
            headers={"Content-Type": "audio/wav"},
        )
        assert r.status_code == 202, r.text
        sid = r.json()["id"]

        body = await _wait_for_status(c, sid, "done")
        assert [s["text"] for s in body["segments"]] == ["seg-1", "seg-2"]
        assert body["mode"] == "solo" and body["channels"] == ["mic"]
        assert body["title"] == "memo 2026-10-12"  # the file name, without its extension
        assert body["final_note"] is not None and "**Me:** seg-1" in body["final_note"]["markdown"]

        wav = await c.get(f"/v1/sessions/{sid}/audio/mic.wav")
        assert wav.status_code == 200 and len(wav.content) == 44 + len(TWO_UTTERANCES)

    leftovers = list((server.settings.data_dir / "uploads").glob("*"))
    assert leftovers == []  # the upload and its decoded copy are removed once ingested


async def test_dashboard_can_upload_without_a_device(
    make_server: Callable[..., ServerHandle], sm: SM, tmp_path: Path
) -> None:
    server = make_server(
        llm=FakeLLM().queue("FinalNote", FinalNote(title="T", summary="S")),
        outbox_dir=tmp_path / "outbox",
        finalize_recover_max_age_s=0,
    )
    async with await web_client(server, sm, OWNER_USER_ID) as web:
        r = await web.post(
            "/v1/sessions/upload",
            params={"title": "Kitchen chat", "mode": "meeting"},
            content=wav_bytes(TWO_UTTERANCES),
        )
        assert r.status_code == 202, r.text
        body = await _wait_for_status(web, r.json()["id"], "done")
    assert body["title"] == "Kitchen chat" and body["mode"] == "meeting"
    assert len(body["segments"]) == 2
    async with sm() as db:
        row = await db.get(SessionRow, uuid.UUID(body["id"]))
        assert row is not None and row.device_id is None and row.user_id == OWNER_USER_ID


async def test_a_file_that_is_not_audio_is_refused_and_creates_no_session(
    make_server: Callable[..., ServerHandle], token: str, sm: SM
) -> None:
    server = make_server()
    async with sm() as db:
        before = (await db.execute(select(func.count()).select_from(SessionRow))).scalar_one()
    async with device_client(server, token) as c:
        r = await c.post("/v1/sessions/upload", content=b"definitely not audio" * 100)
    assert r.status_code == 415, r.text
    async with sm() as db:
        after = (await db.execute(select(func.count()).select_from(SessionRow))).scalar_one()
    assert after == before
    assert list((server.settings.data_dir / "uploads").glob("*")) == []


async def test_an_empty_upload_is_refused(
    make_server: Callable[..., ServerHandle], token: str
) -> None:
    server = make_server()
    async with device_client(server, token) as c:
        r = await c.post("/v1/sessions/upload", content=b"")
    assert r.status_code == 422, r.text


async def test_an_upload_over_the_size_limit_is_refused(
    make_server: Callable[..., ServerHandle], token: str
) -> None:
    server = make_server(upload_max_mb=1)
    async with device_client(server, token) as c:
        r = await c.post("/v1/sessions/upload", content=bytes(1024 * 1024 + 1))
    assert r.status_code == 413, r.text
    assert list((server.settings.data_dir / "uploads").glob("*")) == []


# --- backpressure: an upload must not queue a whole recording's audio in memory ---------------


class BlockedSTT:
    """Transcribes nothing until ``release`` is set."""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def transcribe(self, pcm: bytes, prompt: str) -> Transcription:
        await self.release.wait()
        return Transcription(text="", tier="fake")

    def current_tier(self) -> str | None:
        return "fake"


async def test_pipeline_counts_the_segments_still_waiting_for_stt() -> None:
    stt = BlockedSTT()
    pipeline = SessionPipeline(
        uuid.uuid4(),
        ["mic"],
        settings=Settings(),
        sessionmaker=None,
        bus=Bus(),
        stt=stt,  # type: ignore[arg-type]
    )
    pipeline.start()
    try:
        pipeline.feed("mic", TWO_UTTERANCES)
        assert pipeline.pending_segments() == 2
        stt.release.set()
        await eventually(lambda: pipeline.pending_segments() == 0)
    finally:
        await pipeline.close()


async def test_pipeline_has_transcribed_only_the_audio_before_its_first_waiting_segment() -> None:
    stt = BlockedSTT()
    pipeline = SessionPipeline(
        uuid.uuid4(),
        ["mic"],
        settings=Settings(),
        sessionmaker=None,
        bus=Bus(),
        stt=stt,  # type: ignore[arg-type]
    )
    pipeline.start()
    try:
        assert pipeline.transcribed_ms() == 0
        pipeline.feed("mic", TWO_UTTERANCES)
        assert pipeline.transcribed_ms() <= 1000  # the first utterance starts after 1 s silence
        assert pipeline.fed_ms() == pipeline.ms_of(len(TWO_UTTERANCES)) == 7680  # 30 ms frames
        stt.release.set()
        await eventually(lambda: pipeline.transcribed_ms() == pipeline.fed_ms())
    finally:
        await pipeline.close()


async def test_pipeline_reaches_all_its_audio_when_its_channels_differ_in_length() -> None:
    pipeline = SessionPipeline(
        uuid.uuid4(),
        ["mic", "system"],
        settings=Settings(),
        sessionmaker=None,
        bus=Bus(),
        stt=BlockedSTT(),  # type: ignore[arg-type]
    )
    pipeline.start()
    try:
        pipeline.feed("mic", silence(2.0))
        pipeline.feed("system", silence(5.0))
        assert pipeline.transcribed_ms() == pipeline.fed_ms() == 1980 + 4980  # 30 ms frames
    finally:
        await pipeline.close()


class FakeIngest:
    """Stands in for the hub's per-session state, with a settable STT backlog."""

    def __init__(self) -> None:
        self.fed = 0
        self.backlog = 0
        self.done_ms = 0
        self.uploading = False
        self.store = SimpleNamespace(append=self._append)
        self.pipeline = SimpleNamespace(
            feed=self._feed,
            pending_segments=lambda: self.backlog,
            transcribed_ms=lambda: self.done_ms,
            ms_of=lambda n: n // BYTES_PER_MS,
            finish=self._finish,
        )

    async def _append(self, frame: object) -> str:
        return "stored"

    def _feed(self, channel: str, pcm: bytes) -> None:
        self.fed += 1

    async def _finish(self) -> None:
        pass


class FakeHub:
    def __init__(self, ing: FakeIngest | Exception) -> None:
        self.ing = ing
        self.ended: list[uuid.UUID] = []

    async def get(self, session_id: uuid.UUID, channels: list[str]) -> FakeIngest:
        if isinstance(self.ing, Exception):
            raise self.ing
        return self.ing

    async def end(self, session_id: uuid.UUID) -> bool:
        self.ended.append(session_id)
        return True


async def test_upload_stops_feeding_while_stt_is_behind(tmp_path: Path) -> None:
    pcm = tmp_path / "x.pcm"
    pcm.write_bytes(bytes(MAX_PAYLOAD * 10))
    ing = FakeIngest()
    ing.backlog = UPLOAD_MAX_BACKLOG
    hub = FakeHub(ing)
    uploads = Uploads(hub)  # type: ignore[arg-type]
    sid = uuid.uuid4()

    uploads.start(sid, pcm)
    await asyncio.sleep(0.3)
    assert ing.fed == 0 and ing.uploading  # waiting for STT to catch up

    ing.backlog = 0
    await eventually(lambda: hub.ended == [sid])
    assert ing.fed == 10 and not ing.uploading and not pcm.exists()


async def test_upload_reports_how_much_of_its_recording_is_transcribed(tmp_path: Path) -> None:
    pcm = tmp_path / "x.pcm"
    pcm.write_bytes(bytes(MAX_PAYLOAD * 10))
    ing = FakeIngest()
    ing.backlog = UPLOAD_MAX_BACKLOG
    hub = FakeHub(ing)
    uploads = Uploads(hub)  # type: ignore[arg-type]
    sid = uuid.uuid4()

    assert uploads.progress(sid) is None
    uploads.start(sid, pcm)
    await eventually(lambda: ing.uploading)
    ing.done_ms = 5 * FRAME_MS
    assert uploads.progress(sid) == 0.5

    ing.backlog = 0
    await eventually(lambda: hub.ended == [sid])
    assert uploads.progress(sid) is None


async def test_upload_that_cannot_start_still_removes_its_audio(tmp_path: Path) -> None:
    pcm = tmp_path / "x.pcm"
    pcm.write_bytes(bytes(MAX_PAYLOAD))
    hub = FakeHub(RuntimeError("database is down"))
    uploads = Uploads(hub)  # type: ignore[arg-type]

    uploads.start(uuid.uuid4(), pcm)
    await eventually(lambda: not pcm.exists())
    await uploads.shutdown()


# --- the Wear OS watch: source tag, retry idempotency, its AAC format -------------------------


def aac_adts_bytes(pcm: bytes) -> bytes:
    """What the watch records: raw AAC-LC (ADTS), 16 kHz mono, low bitrate."""
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    proc = subprocess.run(  # noqa: S603
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "wav", "-i", "pipe:0"]  # noqa: S607
        + ["-c:a", "aac", "-b:a", "24k", "-f", "adts", "pipe:1"],
        input=wav_bytes(pcm),
        capture_output=True,
        check=True,
    )
    return proc.stdout


async def test_watch_upload_is_tagged_and_lands_as_a_note_like_any_other_capture(
    make_server: Callable[..., ServerHandle], token: str, tmp_path: Path
) -> None:
    outbox = tmp_path / "outbox"
    server = make_server(
        llm=FakeLLM().queue("FinalNote", FinalNote(title="Watch memo", summary="From the wrist.")),
        outbox_dir=outbox,
        finalize_recover_max_age_s=0,
    )
    async with device_client(server, token) as c:
        r = await c.post(
            "/v1/sessions/upload",
            params={"filename": "watch-1.aac", "source": "watch", "client_id": "w-0001"},
            content=aac_adts_bytes(TWO_UTTERANCES),
        )
        assert r.status_code == 202, r.text
        sid = r.json()["id"]

        body = await _wait_for_status(c, sid, "done")
        assert body["source"] == "watch"
        assert len(body["segments"]) == 2
        listed = (await c.get("/v1/sessions")).json()["items"]
        assert [s["source"] for s in listed if s["id"] == sid] == ["watch"]

    notes = list(outbox.glob("*.md"))
    assert len(notes) == 1
    text = notes[0].read_text()
    assert "source: watch\n" in text
    assert "tags: [ai-output, jarvis-live, inbox]" in text  # the same inbox capture as the rest


async def test_repeating_an_upload_with_the_same_client_id_makes_no_second_session(
    make_server: Callable[..., ServerHandle], token: str, sm: SM
) -> None:
    server = make_server(
        llm=FakeLLM().queue("FinalNote", FinalNote(title="T", summary="S")),
        finalize_recover_max_age_s=0,
    )
    async with device_client(server, token) as c:
        kw = {
            "params": {"source": "watch", "client_id": "w-dup"},
            "content": wav_bytes(TWO_UTTERANCES),
        }
        first = await c.post("/v1/sessions/upload", **kw)
        again = await c.post("/v1/sessions/upload", **kw)
    assert first.status_code == 202 and again.status_code == 202
    assert again.json()["id"] == first.json()["id"]  # the ack the watch needs, a second time
    async with sm() as db:
        n = (
            await db.execute(
                select(func.count()).select_from(SessionRow).where(SessionRow.client_id == "w-dup")
            )
        ).scalar_one()
    assert n == 1


async def test_upload_without_a_source_has_none_and_an_unknown_source_is_refused(
    make_server: Callable[..., ServerHandle], token: str
) -> None:
    server = make_server(
        llm=FakeLLM().queue("FinalNote", FinalNote(title="T", summary="S")),
        finalize_recover_max_age_s=0,
    )
    async with device_client(server, token) as c:
        bad = await c.post(
            "/v1/sessions/upload", params={"source": "toaster"}, content=wav_bytes(TWO_UTTERANCES)
        )
        assert bad.status_code == 422
        ok = await c.post("/v1/sessions/upload", content=wav_bytes(TWO_UTTERANCES))
        assert (await c.get(f"/v1/sessions/{ok.json()['id']}")).json()["source"] is None
