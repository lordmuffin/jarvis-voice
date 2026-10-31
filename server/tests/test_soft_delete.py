"""Soft delete: hide a session, keep its audio and transcript, bring it back."""

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import httpx
import pytest
import websockets
from alembic import command
from sqlalchemy import func, select

from jarvis_live.db.models import SegmentRow
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.db.session import alembic_config
from jarvis_live.users import create_user
from tests.conftest import ServerHandle
from tests.fakes import add_segments, make_session, new_device
from tests.work_item_helpers import SETTINGS, SM, seed_item, web_client

PCM = b"\x01\x02" * 1600


class Rig:
    def __init__(self, server: ServerHandle, sm: SM, user: uuid.UUID, sid: uuid.UUID) -> None:
        self.server, self.sm, self.user, self.sid = server, sm, user, sid

    def client(self, user: uuid.UUID | None = None) -> "asyncio.Future[httpx.AsyncClient]":
        return web_client(self.server, self.sm, user or self.user)  # type: ignore[return-value]

    @property
    def audio(self) -> Path:
        return self.server.settings.data_dir / "sessions" / str(self.sid) / "mic.pcm"


@pytest.fixture
async def rig(make_server: Callable[..., ServerHandle], sm: SM) -> Rig:
    server = make_server(**SETTINGS)
    user = (await create_user(sm, "Alice")).id
    device, _ = await new_device(sm, "alice-phone", user_id=user)
    sid = await make_session(sm, device, status="done")
    await add_segments(sm, sid, [(0, 900, "me", "hello there")])
    r = Rig(server, sm, user, sid)
    r.audio.parent.mkdir(parents=True)
    r.audio.write_bytes(PCM)
    return r


async def test_delete_hides_the_session_everywhere(rig: Rig) -> None:
    async with await rig.client() as c:
        ticket = (await c.post(f"/v1/sessions/{rig.sid}/ticket", json={"role": "viewer"})).json()
        assert (await c.delete(f"/v1/sessions/{rig.sid}")).status_code == 204
        assert (await c.get("/v1/sessions")).json() == {
            "items": [],
            "total": 0,
            "limit": 20,
            "offset": 0,
        }
        for path in (
            f"/v1/sessions/{rig.sid}",
            f"/v1/sessions/{rig.sid}/processing",
            f"/v1/sessions/{rig.sid}/audio/mic.wav",
            f"/v1/sessions/{rig.sid}/work-items",
        ):
            assert (await c.get(path)).status_code == 404, path
        assert (
            await c.post(f"/v1/sessions/{rig.sid}/ticket", json={"role": "viewer"})
        ).status_code == 404
        assert (await c.patch(f"/v1/sessions/{rig.sid}", json={"title": "x"})).status_code == 404
    ws = rig.server.url.replace("http", "ws", 1) + f"/v1/sessions/{rig.sid}/stream"
    with pytest.raises(
        (websockets.exceptions.InvalidStatus, websockets.exceptions.ConnectionClosed)
    ):
        async with websockets.connect(f"{ws}?ticket={ticket['ticket']}") as sock:
            await sock.recv()


async def test_delete_keeps_the_audio_and_the_transcript(rig: Rig) -> None:
    async with await rig.client() as c:
        assert (await c.delete(f"/v1/sessions/{rig.sid}")).status_code == 204
    assert rig.audio.read_bytes() == PCM
    async with rig.sm() as db:
        n = await db.scalar(
            select(func.count()).select_from(SegmentRow).where(SegmentRow.session_id == rig.sid)
        )
        row = await db.get(SessionRow, rig.sid)
    assert n == 1 and row is not None and row.deleted_at is not None


async def test_recently_deleted_lists_only_the_callers_deleted_sessions(rig: Rig) -> None:
    device, _ = await new_device(rig.sm, "alice-mac", user_id=rig.user)
    kept = await make_session(rig.sm, device, status="done")
    bob = (await create_user(rig.sm, "Bob")).id
    bdev, _ = await new_device(rig.sm, "bob-phone", user_id=bob)
    bobs = await make_session(rig.sm, bdev, status="done")
    async with await rig.client(bob) as c:
        assert (await c.delete(f"/v1/sessions/{bobs}")).status_code == 204
    async with await rig.client() as c:
        assert (await c.delete(f"/v1/sessions/{rig.sid}")).status_code == 204
        gone = (await c.get("/v1/sessions", params={"deleted": "true"})).json()
        assert [s["id"] for s in gone["items"]] == [str(rig.sid)] and gone["total"] == 1
        assert gone["items"][0]["deleted_at"] is not None
        live = (await c.get("/v1/sessions")).json()
        assert [s["id"] for s in live["items"]] == [str(kept)]
        assert live["items"][0]["deleted_at"] is None


async def test_restore_brings_the_session_back_intact(rig: Rig) -> None:
    async with await rig.client() as c:
        await c.delete(f"/v1/sessions/{rig.sid}")
        r = await c.post(f"/v1/sessions/{rig.sid}/restore")
        assert r.status_code == 200 and r.json()["deleted_at"] is None
        detail = (await c.get(f"/v1/sessions/{rig.sid}")).json()
        assert [s["text"] for s in detail["segments"]] == ["hello there"]
        assert detail["audio"]["mic"] is True
        assert (await c.get(f"/v1/sessions/{rig.sid}/audio/mic.wav")).status_code == 200
        assert (await c.get("/v1/sessions", params={"deleted": "true"})).json()["total"] == 0


async def test_restoring_a_session_that_is_not_deleted_is_404(rig: Rig) -> None:
    async with await rig.client() as c:
        assert (await c.post(f"/v1/sessions/{rig.sid}/restore")).status_code == 404


async def test_delete_is_idempotent_and_keeps_the_first_time(rig: Rig) -> None:
    async with await rig.client() as c:
        assert (await c.delete(f"/v1/sessions/{rig.sid}")).status_code == 204
        first = (await c.get("/v1/sessions", params={"deleted": "true"})).json()["items"][0]
        assert (await c.delete(f"/v1/sessions/{rig.sid}")).status_code == 204
        again = (await c.get("/v1/sessions", params={"deleted": "true"})).json()["items"][0]
    assert first["deleted_at"] == again["deleted_at"]


@pytest.mark.parametrize("status", ["streaming", "finalizing"])
async def test_a_session_still_recording_cannot_be_deleted(rig: Rig, status: str) -> None:
    device, _ = await new_device(rig.sm, f"dev-{status}", user_id=rig.user)
    sid = await make_session(rig.sm, device, status="live" if status == "streaming" else status)
    async with await rig.client() as c:
        assert (await c.delete(f"/v1/sessions/{sid}")).status_code == 409
        assert (await c.get(f"/v1/sessions/{sid}")).status_code == 200


async def test_the_owner_can_preview_a_deleted_session_for_restoring(rig: Rig) -> None:
    async with await rig.client() as c:
        await c.delete(f"/v1/sessions/{rig.sid}")
        r = await c.get(f"/v1/sessions/{rig.sid}", params={"include_deleted": "true"})
        assert r.status_code == 200 and r.json()["deleted_at"] is not None
    other = (await create_user(rig.sm, "Eve")).id
    async with await rig.client(other) as c:
        r = await c.get(f"/v1/sessions/{rig.sid}", params={"include_deleted": "true"})
        assert r.status_code == 404


async def test_other_users_cannot_delete_restore_or_list_a_deleted_session(rig: Rig) -> None:
    other = (await create_user(rig.sm, "Eve")).id
    async with await rig.client() as c:
        await c.delete(f"/v1/sessions/{rig.sid}")
    async with await rig.client(other) as c:
        assert (await c.delete(f"/v1/sessions/{rig.sid}")).status_code == 404
        assert (await c.post(f"/v1/sessions/{rig.sid}/restore")).status_code == 404
        assert (await c.get("/v1/sessions", params={"deleted": "true"})).json()["total"] == 0
        assert (
            await c.get("/v1/sessions", params={"deleted": "true", "all": "true"})
        ).status_code == 403
    async with rig.sm() as db:
        row = await db.get(SessionRow, rig.sid)
        assert row is not None and row.deleted_at is not None  # still deleted, not restored


async def test_admins_see_everyones_deleted_sessions_but_cannot_delete_or_restore_them(
    rig: Rig,
) -> None:
    admin = (await create_user(rig.sm, "Root", is_admin=True)).id
    async with await rig.client() as c:
        await c.delete(f"/v1/sessions/{rig.sid}")
    async with await rig.client(admin) as c:
        page = (await c.get("/v1/sessions", params={"deleted": "true", "all": "true"})).json()
        assert str(rig.sid) in [s["id"] for s in page["items"]]
        assert (await c.post(f"/v1/sessions/{rig.sid}/restore")).status_code == 403
    async with await rig.client() as c:
        await c.post(f"/v1/sessions/{rig.sid}/restore")
    async with await rig.client(admin) as c:
        assert (await c.delete(f"/v1/sessions/{rig.sid}")).status_code == 403


async def test_work_items_of_a_deleted_session_leave_the_users_list(rig: Rig) -> None:
    await seed_item(rig.sm, rig.sid, rig.user)
    async with await rig.client() as c:
        assert len((await c.get("/v1/work-items")).json()["items"]) == 1
        await c.delete(f"/v1/sessions/{rig.sid}")
        assert (await c.get("/v1/work-items")).json()["items"] == []
        await c.post(f"/v1/sessions/{rig.sid}/restore")
        assert len((await c.get("/v1/work-items")).json()["items"]) == 1


async def test_a_delete_after_date_can_be_set_and_cleared(rig: Rig) -> None:
    async with await rig.client() as c:
        r = await c.patch(f"/v1/sessions/{rig.sid}", json={"delete_after_days": 7})
        assert r.status_code == 200, r.text
        due = datetime.fromisoformat(r.json()["purge_after"])
        assert 6.9 < (due - datetime.now(UTC)).total_seconds() / 86_400 < 7.1
        assert r.json()["title"] is None  # a PATCH without a title leaves it alone
        await c.patch(f"/v1/sessions/{rig.sid}", json={"title": "kept"})
        assert (await c.get(f"/v1/sessions/{rig.sid}")).json()["purge_after"] is not None
        r = await c.patch(f"/v1/sessions/{rig.sid}", json={"delete_after_days": None})
        assert r.json()["purge_after"] is None and r.json()["title"] == "kept"
        for bad in (0, -1, 4000):
            r = await c.patch(f"/v1/sessions/{rig.sid}", json={"delete_after_days": bad})
            assert r.status_code == 422


async def test_restore_cancels_a_pending_delete_after(rig: Rig) -> None:
    async with await rig.client() as c:
        await c.patch(f"/v1/sessions/{rig.sid}", json={"delete_after_days": 3})
        await c.delete(f"/v1/sessions/{rig.sid}")
        r = await c.post(f"/v1/sessions/{rig.sid}/restore")
        assert r.json()["purge_after"] is None


async def test_migration_adds_the_columns_and_keeps_existing_rows(postgres_url: str) -> None:
    dsn = postgres_url.replace("postgresql+asyncpg://", "postgresql://")
    name = f"mig_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = postgres_url.rsplit("/", 1)[0] + f"/{name}"
    try:
        await asyncio.to_thread(command.upgrade, alembic_config(url), "0009")
        conn = await asyncpg.connect(dsn.rsplit("/", 1)[0] + f"/{name}")
        try:
            sid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO sessions (id, mode, user_id) VALUES ($1, 'solo', "
                "'00000000-0000-0000-0000-000000000001')",
                sid,
            )
            await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
            row = await conn.fetchrow("SELECT deleted_at, purge_after FROM sessions")
            assert (row["deleted_at"], row["purge_after"]) == (None, None)
            await asyncio.to_thread(command.downgrade, alembic_config(url), "0009")
            assert await conn.fetchval("SELECT count(*) FROM sessions") == 1
            await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
        finally:
            await conn.close()
    finally:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
