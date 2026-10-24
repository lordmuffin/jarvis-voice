"""``processing`` on a session, and ``POST /v1/sessions/{id}/process``: the real app."""

import asyncio
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from jarvis_live.copilot.schema import FinalNote
from jarvis_live.db.models import OWNER_USER_ID
from jarvis_live.stt.backend import Transcription
from tests.audio import TWO_UTTERANCES
from tests.conftest import FakeSTT, ServerHandle
from tests.fakes import FakeLLM, add_segments, make_session, new_device
from tests.test_publish import SM, client, finished
from tests.test_upload import wav_bytes


class GatedSTT(FakeSTT):
    """Holds every transcription until ``gate`` is set (from the test's own event loop)."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()

    async def transcribe(self, pcm: bytes, prompt: str) -> Transcription:
        while not self.gate.is_set():
            await asyncio.sleep(0.01)
        return await super().transcribe(pcm, prompt)


IDLE = {"transcription": "idle", "summary": "idle", "started_at": None, "job_source": None}


async def _until(
    c: httpx.AsyncClient, sid: Any, ok: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    for _ in range(400):
        d: dict[str, Any] = (await c.get(f"/v1/sessions/{sid}")).json()
        if ok(d):
            return d
        await asyncio.sleep(0.05)
    raise AssertionError(f"never reached: {d['status']} {d['processing']}")


def _llm() -> FakeLLM:
    return FakeLLM().queue("FinalNote", FinalNote(title="Again", summary="S."))


async def _done_with_audio(sm: SM, server: ServerHandle) -> uuid.UUID:
    device_id = (await new_device(sm, f"mac-{uuid.uuid4()}", user_id=OWNER_USER_ID))[0]
    sid = await finished(sm, server.settings.data_dir / "outbox", device_id)
    await add_segments(sm, sid, [(0, 1000, "me", "old words")])
    d = server.settings.data_dir / "sessions" / str(sid)
    d.mkdir(parents=True)
    (d / "mic.pcm").write_bytes(TWO_UTTERANCES)
    return sid


async def test_a_finished_session_reads_idle(
    make_server: Callable[..., ServerHandle], sm: SM, token: str
) -> None:
    server = make_server(llm=_llm(), outbox_dir=Path("unused"))
    sid = await _done_with_audio(sm, server)
    async with client(server, token) as c:
        assert (await c.get(f"/v1/sessions/{sid}")).json()["processing"] == IDLE
        assert (await c.get(f"/v1/sessions/{sid}/processing")).json() == IDLE


async def test_process_transcribes_again_then_summarizes_and_never_runs_twice(
    make_server: Callable[..., ServerHandle], sm: SM, token: str
) -> None:
    stt = GatedSTT()
    server = make_server(stt=stt, llm=_llm(), finalize_recover_max_age_s=0)
    sid = await _done_with_audio(sm, server)
    async with client(server, token) as c:
        r = await c.post(f"/v1/sessions/{sid}/process")
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["started"] is True
        assert body["processing"]["transcription"] == "running"
        assert body["processing"]["summary"] == "queued"
        assert body["processing"]["job_source"] == "reprocess"
        assert body["processing"]["started_at"] is not None

        again = await c.post(f"/v1/sessions/{sid}/process")
        assert again.status_code == 202
        assert again.json()["started"] is False
        assert again.json()["processing"] == body["processing"]
        assert (await c.post(f"/v1/sessions/{sid}/regenerate")).status_code == 409

        stt.gate.set()
        d = await _until(c, sid, lambda d: d["processing"] == IDLE and d["status"] == "done")
        assert [s["text"] for s in d["segments"]] == ["seg-1", "seg-2"]
        assert d["final_note"]["title"] == "Again"
    assert stt.calls == 2  # one run: the second POST started nothing


async def test_process_joins_an_upload_still_being_transcribed(
    make_server: Callable[..., ServerHandle], sm: SM, token: str
) -> None:
    stt = GatedSTT()
    server = make_server(stt=stt, llm=_llm(), finalize_recover_max_age_s=0)
    async with client(server, token) as c:
        r = await c.post(
            "/v1/sessions/upload",
            params={"filename": "memo.wav"},
            content=wav_bytes(TWO_UTTERANCES),
            headers={"Content-Type": "audio/wav"},
        )
        sid = r.json()["id"]
        d = await _until(c, sid, lambda d: d["processing"]["job_source"] == "upload")
        assert d["processing"]["transcription"] == "running"
        assert d["processing"]["summary"] == "queued"

        r = await c.post(f"/v1/sessions/{sid}/process")
        assert r.status_code == 202 and r.json()["started"] is False
        assert r.json()["processing"]["job_source"] == "upload"

        stt.gate.set()
        d = await _until(c, sid, lambda d: d["processing"] == IDLE and d["status"] == "done")
        assert [s["text"] for s in d["segments"]] == ["seg-1", "seg-2"]
    assert stt.calls == 2


async def test_process_without_stored_audio_only_summarizes(
    make_server: Callable[..., ServerHandle], sm: SM, token: str
) -> None:
    server = make_server(llm=_llm(), finalize_recover_max_age_s=0)
    device_id = (await new_device(sm, f"mac-{uuid.uuid4()}", user_id=OWNER_USER_ID))[0]
    sid = await finished(sm, server.settings.data_dir / "outbox", device_id)
    await add_segments(sm, sid, [(0, 1000, "me", "kept words")])
    async with client(server, token) as c:
        r = await c.post(f"/v1/sessions/{sid}/process")
        assert r.status_code == 202 and r.json()["started"] is True
        assert r.json()["processing"]["transcription"] == "idle"
        assert r.json()["processing"]["job_source"] == "regenerate"
        d = await _until(c, sid, lambda d: d["processing"] == IDLE and d["status"] == "done")
        assert [s["text"] for s in d["segments"]] == ["kept words"]
        assert d["final_note"]["title"] == "Again"


async def test_process_is_refused_where_it_cannot_run(
    make_server: Callable[..., ServerHandle], sm: SM, token: str
) -> None:
    device_id = (await new_device(sm, f"mac-{uuid.uuid4()}", user_id=OWNER_USER_ID))[0]
    live = await make_session(sm, device_id, status="live")  # no producer connected
    local = await make_session(sm, device_id, status="done", local_only=True)
    server = make_server(llm=_llm())
    async with client(server, token) as c:
        assert (await c.post(f"/v1/sessions/{live}/process")).status_code == 409
        assert (await c.post(f"/v1/sessions/{local}/process")).status_code == 409
        assert (await c.post(f"/v1/sessions/{uuid.uuid4()}/process")).status_code == 404
    no_llm = make_server()
    async with client(no_llm, token) as c:
        assert (await c.post(f"/v1/sessions/{local}/process")).status_code == 503
