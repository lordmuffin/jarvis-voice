"""Restricted sessions (work calls): recorded on the Mac or phone, uploaded on purpose. They get
a note, but it is tagged and filed apart, and nothing leaves the homelab: no vault PR, no work
items, no Gotify body."""

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jarvis_live.api import sessions as sessions_api
from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.copilot.schema import FinalNote
from jarvis_live.db.models import OWNER_USER_ID, CopilotItem, User
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.orchestrator import Orchestrator
from jarvis_live.publish.service import PublishService
from jarvis_live.users import create_user
from tests.audio import TWO_UTTERANCES
from tests.conftest import ServerHandle
from tests.fakes import FakeLLM, FakeNotifier, add_segments, make_session, new_device
from tests.test_copilot_loop import FakeVault
from tests.test_finalizer import NOTE, Rig
from tests.test_publish import client, finished, outbox_of
from tests.test_upload import wav_bytes
from tests.work_item_helpers import device_client

SM = async_sessionmaker  # type: ignore[type-arg]


@pytest.fixture
def mk(sm: SM, tmp_path: Path) -> Any:
    def make(llm: FakeLLM | None = None, **cfg: Any) -> Rig:
        return Rig(sm, tmp_path, llm or FakeLLM().queue("FinalNote", NOTE), **cfg)

    return make


# --- how a session becomes restricted ---------------------------------------------------------


async def test_a_session_created_restricted_says_so(server: ServerHandle, token: str) -> None:
    async with client(server, token) as c:
        body = {"mode": "meeting", "channels": ["mic"]}
        plain = (await c.post("/v1/sessions", json=body)).json()["id"]
        marked = (await c.post("/v1/sessions", json={**body, "restricted": True})).json()["id"]
        assert (await c.get(f"/v1/sessions/{plain}")).json()["restricted"] is False
        assert (await c.get(f"/v1/sessions/{marked}")).json()["restricted"] is True
        listed = {s["id"]: s["restricted"] for s in (await c.get("/v1/sessions")).json()["items"]}
        assert listed[marked] is True and listed[plain] is False


async def test_an_uploaded_recording_can_be_restricted(
    make_server: Callable[..., ServerHandle], token: str
) -> None:
    server = make_server()
    async with device_client(server, token) as c:
        r = await c.post(
            "/v1/sessions/upload",
            params={"restricted": "true"},
            content=wav_bytes(TWO_UTTERANCES),
            headers={"Content-Type": "audio/wav"},
        )
        assert r.status_code == 202, r.text
        assert (await c.get(f"/v1/sessions/{r.json()['id']}")).json()["restricted"] is True


# --- the note: tagged, filed apart, quiet -----------------------------------------------------


async def test_a_restricted_note_is_tagged_filed_apart_and_pushes_no_content(mk: Any) -> None:
    rig: Rig = mk()
    sid = await rig.session(restricted=True)
    await add_segments(rig.sm, sid, [(0, 3000, "me", "Send the quote.")])

    result = await rig.fin.run(sid)

    assert result is not None and result.path == "Restricted/2026-10-09 - Vendor call.md"
    text = (rig.settings.outbox_dir / result.path).read_text()  # type: ignore[operator]
    assert "restricted: true" in text
    assert "tags: [ai-output, jarvis-live, inbox, restricted]" in text
    assert rig.files() == []  # nothing in the outbox root, where the publisher looks
    assert len(rig.llm.calls) == 1  # it is still summarised, on the homelab
    (sent,) = rig.notifier.sent
    assert "Vendor call" not in sent.title + sent.message
    assert NOTE.summary not in sent.message
    assert sent.url == f"https://jarvis.example/sessions/{sid}"


async def test_other_users_restricted_notes_are_filed_apart_too(mk: Any, sm: SM) -> None:
    rig: Rig = mk()
    user = await create_user(sm, "Sam")
    assert user.notes_subdir
    sid = await rig.session(user_id=user.id, restricted=True)
    await add_segments(rig.sm, sid, [(0, 3000, "me", "hello")])
    result = await rig.fin.run(sid)
    assert (
        result is not None
        and result.path == f"Restricted - {user.notes_subdir}/2026-10-09 - Vendor call.md"
    )


async def test_an_unrestricted_note_is_unchanged(mk: Any) -> None:
    rig: Rig = mk()
    sid = await rig.session()
    await add_segments(rig.sm, sid, [(0, 3000, "me", "hello")])
    result = await rig.fin.run(sid)
    assert result is not None and result.path == "2026-10-09 - Vendor call.md"
    assert "restricted" not in (rig.settings.outbox_dir / result.path).read_text()  # type: ignore[operator]


# --- nothing leaves the homelab ---------------------------------------------------------------


async def test_work_items_are_not_mined_from_restricted_sessions(sm: SM, tmp_path: Path) -> None:
    orch = Orchestrator(
        Settings(outbox_dir=tmp_path, work_items_enabled=True),
        sm,
        Bus(),
        SimpleNamespace(),  # type: ignore[arg-type]
        FakeLLM(),
        FakeVault(),
        FakeNotifier(),
    )
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}", user_id=OWNER_USER_ID)
    open_id = await make_session(sm, device_id)
    shut_id = await make_session(sm, device_id, restricted=True)
    async with sm() as db:
        open_row, shut_row = await db.get(SessionRow, open_id), await db.get(SessionRow, shut_id)
    assert open_row and shut_row
    assert await orch._ideas_eligible(open_row) is True
    assert await orch._ideas_eligible(shut_row) is False


async def test_restricted_notes_cannot_be_published(
    make_server: Callable[..., ServerHandle],
    publish_settings: dict[str, Any],
    github: Any,
    sm: SM,
    token: str,
) -> None:
    server = make_server(**publish_settings)
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    sid = await finished(sm, outbox_of(server), device_id)
    async with sm() as db:
        row = await db.get(SessionRow, sid)
        assert row is not None
        row.restricted = True
        await db.commit()
    async with client(server, token) as c:
        d = (await c.get(f"/v1/sessions/{sid}")).json()
        assert d["can_publish"] is False
        r = await c.post(f"/v1/sessions/{sid}/publication")
        assert r.status_code == 409 and "restricted" in r.text
        q = (await c.get("/v1/publishing")).json()
        assert str(sid) not in {i["session_id"] for i in q["ready"]}
    assert github.pulls == {}


async def test_auto_publish_skips_a_restricted_note(
    make_server: Callable[..., ServerHandle],
    publish_settings: dict[str, Any],
    github: Any,
    sm: SM,
    token: str,
) -> None:
    llm = FakeLLM().queue("FinalNote", FinalNote(title="Quiet", summary="S."))
    server = make_server(llm=llm, finalize_recover_max_age_s=0, **publish_settings)
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    sid = await make_session(sm, device_id, status="live", restricted=True)
    await add_segments(sm, sid, [(0, 1000, "me", "hello")])
    try:
        async with client(server, token) as c:
            await c.patch("/v1/me", json={"auto_publish": True})
            assert (await c.post(f"/v1/sessions/{sid}/end")).status_code == 200
            for _ in range(200):
                d = (await c.get(f"/v1/sessions/{sid}")).json()
                if d["status"] == "done":
                    break
                await asyncio.sleep(0.05)
            assert d["status"] == "done" and d["final_note"] is not None
            await asyncio.sleep(0.3)
            assert (await c.get(f"/v1/sessions/{sid}")).json()["publication"] is None
        assert github.pulls == {}
    finally:
        async with sm() as db:
            await db.execute(
                update(User).where(User.id == OWNER_USER_ID).values(auto_publish=False)
            )
            await db.commit()


# --- lifting the restriction ------------------------------------------------------------------


RESTRICTED_NOTE = (
    "---\nsession_id: {sid}\ntype: ai-output\nrestricted: true\n"
    "tags: [ai-output, jarvis-live, inbox, restricted]\n---\n\n# Vendor call\n\nBody.\n"
)


async def restricted_session(
    sm: SM, outbox: Path, device_id: uuid.UUID, *, status: str = "done"
) -> uuid.UUID:
    """A restricted session with its note filed in ``Restricted/``, as the finalizer leaves it."""
    sid = await make_session(sm, device_id, status=status, restricted=True)
    rel = "Restricted/2026-10-09 - Vendor call.md"
    (outbox / rel).parent.mkdir(parents=True, exist_ok=True)
    (outbox / rel).write_text(RESTRICTED_NOTE.format(sid=sid))
    async with sm() as db:
        db.add(
            CopilotItem(
                session_id=sid, kind="final_note", payload={"path": rel, "title": "Vendor call"}
            )
        )
        await db.commit()
    return sid


async def test_lifting_the_restriction_refiles_the_note_and_allows_publishing(
    make_server: Callable[..., ServerHandle], publish_settings: dict[str, Any], sm: SM, token: str
) -> None:
    server = make_server(**publish_settings)
    outbox = outbox_of(server)
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    sid = await restricted_session(sm, outbox, device_id)
    async with client(server, token) as c:
        r = await c.post(f"/v1/sessions/{sid}/unrestrict")
        assert r.status_code == 200 and r.json()["restricted"] is False

        d = (await c.get(f"/v1/sessions/{sid}")).json()
        assert d["final_note"]["path"] == "2026-10-09 - Vendor call.md"
        assert d["can_publish"] is True
        md = d["final_note"]["markdown"]
        assert "restricted" not in md and "tags: [ai-output, jarvis-live, inbox]" in md
        assert "# Vendor call" in md and f"session_id: {sid}" in md
        q = (await c.get("/v1/publishing")).json()
        assert str(sid) in {i["session_id"] for i in q["ready"]}
    assert not (outbox / "Restricted/2026-10-09 - Vendor call.md").exists()
    assert (outbox / "2026-10-09 - Vendor call.md").exists()


async def test_lifting_a_restriction_that_is_not_there_changes_nothing(
    server: ServerHandle, sm: SM, token: str
) -> None:
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    sid = await make_session(sm, device_id, status="done")
    async with client(server, token) as c:
        r = await c.post(f"/v1/sessions/{sid}/unrestrict")
        assert r.status_code == 200 and r.json()["restricted"] is False


async def test_only_the_owner_can_lift_it_and_not_while_the_note_is_being_written(
    server: ServerHandle, sm: SM, token: str
) -> None:
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    busy = await make_session(sm, device_id, status="finalizing", restricted=True)
    other = await create_user(sm, "Sam")
    other_device, other_token = await new_device(sm, "sam-mac", user_id=other.id)
    theirs = await make_session(sm, other_device, status="done", restricted=True)
    async with client(server, token) as c:
        assert (await c.post(f"/v1/sessions/{busy}/unrestrict")).status_code == 409
        assert (await c.post(f"/v1/sessions/{theirs}/unrestrict")).status_code == 404
    async with client(server, other_token) as c:
        assert (await c.post(f"/v1/sessions/{theirs}/unrestrict")).status_code == 200


async def test_a_session_still_recording_cannot_be_lifted(
    server: ServerHandle, sm: SM, token: str
) -> None:
    """Its note does not exist yet: lifting now would race the finalizer for where it goes."""
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    live = await make_session(sm, device_id, status="live", restricted=True)
    async with client(server, token) as c:
        assert (await c.post(f"/v1/sessions/{live}/unrestrict")).status_code == 409
        assert (await c.get(f"/v1/sessions/{live}")).json()["restricted"] is True


class SpyLock:
    def __init__(self, events: list[str], on_enter: Callable[[], Any] | None = None) -> None:
        self.events = events
        self.on_enter = on_enter

    async def __aenter__(self) -> None:
        self.events.append("locked")
        if self.on_enter:
            await self.on_enter()

    async def __aexit__(self, *exc: object) -> None:
        self.events.append("unlocked")


async def test_lifting_runs_under_the_notes_lock(
    server: ServerHandle, sm: SM, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every other note writer holds it, so an edit cannot land between the read and the move."""
    events: list[str] = []

    async def lift(*_: Any) -> None:
        events.append("lifted")

    monkeypatch.setattr(PublishService, "lock", lambda _s, _id: SpyLock(events))
    monkeypatch.setattr(sessions_api, "lift_restriction", lift)
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    sid = await make_session(sm, device_id, status="done", restricted=True)
    async with client(server, token) as c:
        assert (await c.post(f"/v1/sessions/{sid}/unrestrict")).status_code == 200
    assert events == ["locked", "lifted", "unlocked"]


async def test_a_session_reopened_while_waiting_for_the_lock_is_not_lifted(
    server: ServerHandle, sm: SM, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The status that counts is the one read under the lock, not the one read before it."""
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    sid = await make_session(sm, device_id, status="done", restricted=True)

    async def regenerate_gets_there_first() -> None:
        engine = create_async_engine(server.settings.database_url)
        async with engine.begin() as conn:
            await conn.execute(
                update(SessionRow).where(SessionRow.id == sid).values(status="finalizing")
            )
        await engine.dispose()

    monkeypatch.setattr(
        PublishService, "lock", lambda _s, _id: SpyLock([], regenerate_gets_there_first)
    )
    async with client(server, token) as c:
        assert (await c.post(f"/v1/sessions/{sid}/unrestrict")).status_code == 409
        assert (await c.get(f"/v1/sessions/{sid}")).json()["restricted"] is True
