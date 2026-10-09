"""Copilot + finalizer wired into the real app: replay audio, end, get a note."""

import asyncio
from collections.abc import Callable
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.copilot.schema import CopilotDelta, FinalNote, NoteUpsert
from jarvis_live.replay import ReplayProducer
from tests.audio import TWO_UTTERANCES
from tests.conftest import ServerHandle
from tests.fakes import FakeLLM, FakeNotifier
from tests.helpers import eventually, open_viewer

SM = async_sessionmaker  # type: ignore[type-arg]


async def test_session_end_produces_note_and_final_note_event(
    make_server: Callable[..., ServerHandle], token: str, tmp_path: Path
) -> None:
    llm = (
        FakeLLM()
        .queue("CopilotDelta", CopilotDelta(notes_upsert=[NoteUpsert(text="hello")]))
        .queue("FinalNote", FinalNote(title="Standup", summary="Short one."))
    )
    notifier = FakeNotifier()
    outbox = tmp_path / "outbox"
    server = make_server(
        llm=llm,
        notifier=notifier,
        outbox_dir=outbox,
        finalize_recover_max_age_s=0,  # the shared test DB holds other tests' sessions
        status_interval_s=0.1,
        copilot_min_segments=2,
        copilot_poll_s=0.1,
        public_base_url="https://j.example",
    )
    rp = ReplayProducer(server.url, token, speed=50.0)
    sid = await rp.create_session("standup")
    async with await open_viewer(rp, sid) as viewer:
        await rp.run(TWO_UTTERANCES, session_id=sid)
        await eventually(lambda: len(viewer.of_type("final_note")) == 1)
        # copilot snapshot may or may not have landed before the session ended (two segments
        # arrive right at the end); llm_ok must have been reported true meanwhile
        assert all(s["llm_ok"] for s in viewer.of_type("status"))

    (fn,) = viewer.of_type("final_note")
    assert fn["title"] == "Standup"
    note = (outbox / fn["path"]).read_text()
    assert f"session_id: {sid}" in note and "**Me:** seg-1" in note
    assert [(s.priority, s.url) for s in notifier.sent] == [
        (5, f"https://j.example/sessions/{sid}")
    ]
    async with httpx.AsyncClient(base_url=server.url, headers=rp._auth) as c:
        for _ in range(50):
            body = (await c.get(f"/v1/sessions/{sid}")).json()
            if body["status"] == "done":
                break
            await asyncio.sleep(0.05)
    assert body["status"] == "done"


async def test_copilot_snapshot_streams_to_viewer_mid_session(
    make_server: Callable[..., ServerHandle], token: str, tmp_path: Path
) -> None:
    llm = (
        FakeLLM()
        .queue("CopilotDelta", CopilotDelta(notes_upsert=[NoteUpsert(text="first point")]))
        .queue("FinalNote", FinalNote(title="T", summary="S"))
    )
    server = make_server(
        llm=llm,
        outbox_dir=tmp_path / "o",
        finalize_recover_max_age_s=0,
        copilot_min_segments=1,
        copilot_poll_s=0.05,
    )
    rp = ReplayProducer(server.url, token, speed=5.0)  # slow: segment 1 finalizes mid-stream
    sid = await rp.create_session()
    async with await open_viewer(rp, sid) as viewer:
        await rp.run(TWO_UTTERANCES, session_id=sid)
        await eventually(lambda: len(viewer.of_type("final_note")) == 1)
    snaps = viewer.of_type("copilot")
    assert snaps and snaps[-1]["notes"] == [{"id": "n1", "text": "first point"}]
    assert snaps[-1]["version"] >= 1


async def test_settings_driven_wiring_starts_and_builds_the_vault_index(
    make_server: Callable[..., ServerHandle], tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Note.md").write_text("# Note\n\nsome text\n")
    server = make_server(
        litellm_base_url="http://127.0.0.1:9/v1",  # nothing listens; never called here
        vault_clone_dir=vault,
        index_dir=tmp_path / "index",
        gotify_url="http://127.0.0.1:9",
        finalize_recover_max_age_s=0,
    )
    async with httpx.AsyncClient() as c:
        assert (await c.get(f"{server.url}/healthz")).json() == {"status": "ok"}
    await eventually(lambda: (tmp_path / "index" / "vault.sqlite").exists())
