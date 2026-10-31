"""Retention: delete-after, the trash window, and the (unchanged) audio purge."""

import asyncio
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, update

from jarvis_live.config import Settings
from jarvis_live.db.models import (
    AudioChunk,
    CopilotItem,
    MarkerRow,
    NotePublication,
    SegmentRow,
    WorkItem,
)
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.retention import purge_trash, run_retention_loop, soft_delete_due, sweep
from tests.fakes import FakeNotifier, add_segments, make_session, new_device
from tests.work_item_helpers import SM, seed_item

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


class Rig:
    def __init__(self, sm: SM, tmp_path: Path) -> None:
        self.sm, self.data, self.outbox = sm, tmp_path / "data", tmp_path / "data" / "outbox"

    async def session(
        self,
        *,
        deleted_days_ago: float | None = None,
        purge_in_days: float | None = None,
        status: str = "done",
        note: bool = True,
    ) -> uuid.UUID:
        device, _ = await new_device(self.sm, f"dev-{uuid.uuid4().hex[:6]}")
        sid = await make_session(self.sm, device, status=status)
        await add_segments(self.sm, sid, [(0, 900, "me", "hello")])
        d = self.data / "sessions" / str(sid)
        d.mkdir(parents=True)
        (d / "mic.pcm").write_bytes(b"\x01\x02" * 100)
        async with self.sm() as db:
            db.add(MarkerRow(session_id=sid, t_ms=1, label="m"))
            db.add(
                AudioChunk(session_id=sid, channel="mic", seq=0, t_ms=0, byte_offset=0, byte_len=4)
            )
            db.add(CopilotItem(session_id=sid, kind="snapshot", payload={}))
            if note:
                rel = f"{sid.hex[:6]}.md"
                db.add(
                    CopilotItem(
                        session_id=sid, kind="final_note", payload={"path": rel, "title": "t"}
                    )
                )
                self.outbox.mkdir(parents=True, exist_ok=True)
                (self.outbox / rel).write_text(f"---\nsession_id: {sid}\n---\n\nbody\n")
            await db.execute(
                update(SessionRow)
                .where(SessionRow.id == sid)
                .values(
                    deleted_at=None
                    if deleted_days_ago is None
                    else NOW - timedelta(days=deleted_days_ago),
                    purge_after=None
                    if purge_in_days is None
                    else NOW + timedelta(days=purge_in_days),
                )
            )
            await db.commit()
        return sid

    async def exists(self, sid: uuid.UUID) -> bool:
        async with self.sm() as db:
            return await db.get(SessionRow, sid) is not None

    async def rows(self, model: Any, sid: uuid.UUID) -> int:
        async with self.sm() as db:
            return int(
                await db.scalar(
                    select(func.count()).select_from(model).where(model.session_id == sid)
                )
                or 0
            )

    def audio(self, sid: uuid.UUID) -> Path:
        return self.data / "sessions" / str(sid)

    def note(self, sid: uuid.UUID) -> Path:
        return self.outbox / f"{sid.hex[:6]}.md"


@pytest.fixture
def rig(sm: SM, tmp_path: Path) -> Rig:
    return Rig(sm, tmp_path)


# --- the trash window ---------------------------------------------------------------------------


async def test_trash_is_kept_inside_the_window_and_purged_after_it(rig: Rig) -> None:
    inside = await rig.session(deleted_days_ago=29)
    outside = await rig.session(deleted_days_ago=31)
    purged, failed = await purge_trash(rig.sm, rig.data, rig.outbox, 30, now=NOW)
    assert (purged, failed) == (1, 0)
    assert await rig.exists(inside) and rig.audio(inside).is_dir() and rig.note(inside).exists()
    assert not await rig.exists(outside)


async def test_a_purge_removes_rows_audio_and_note_together(rig: Rig) -> None:
    sid = await rig.session(deleted_days_ago=40)
    await seed_item(rig.sm, sid, (await _owner(rig, sid)))
    await purge_trash(rig.sm, rig.data, rig.outbox, 30, now=NOW)
    assert not await rig.exists(sid)
    assert not rig.audio(sid).exists() and not rig.note(sid).exists()
    for model in (SegmentRow, MarkerRow, AudioChunk, CopilotItem, WorkItem):
        assert await rig.rows(model, sid) == 0, model


async def _owner(rig: Rig, sid: uuid.UUID) -> uuid.UUID:
    async with rig.sm() as db:
        row = await db.get(SessionRow, sid)
        assert row is not None
        return row.user_id


async def test_a_purge_touches_nothing_else(rig: Rig) -> None:
    gone = await rig.session(deleted_days_ago=40)
    kept = await rig.session()
    await purge_trash(rig.sm, rig.data, rig.outbox, 30, now=NOW)
    assert not await rig.exists(gone) and await rig.exists(kept)
    assert rig.audio(kept).is_dir() and rig.note(kept).exists()
    assert await rig.rows(SegmentRow, kept) == 1


async def test_a_note_that_is_not_this_sessions_is_left_alone(rig: Rig) -> None:
    sid = await rig.session(deleted_days_ago=40)
    rig.note(sid).write_text("---\nsession_id: someone-else\n---\n")
    await purge_trash(rig.sm, rig.data, rig.outbox, 30, now=NOW)
    assert not await rig.exists(sid) and rig.note(sid).exists()


async def test_a_session_with_an_open_pull_request_is_not_purged(rig: Rig) -> None:
    sid = await rig.session(deleted_days_ago=40)
    async with rig.sm() as db:
        db.add(NotePublication(session_id=sid, state="open"))
        await db.commit()
    assert await purge_trash(rig.sm, rig.data, rig.outbox, 30, now=NOW) == (0, 0)
    assert await rig.exists(sid)
    async with rig.sm() as db:
        await db.execute(update(NotePublication).values(state="merged"))
        await db.commit()
    assert await purge_trash(rig.sm, rig.data, rig.outbox, 30, now=NOW) == (1, 0)


async def test_zero_days_means_no_trash(rig: Rig) -> None:
    sid = await rig.session(deleted_days_ago=0.01)
    assert (await purge_trash(rig.sm, rig.data, rig.outbox, 0, now=NOW))[0] == 1
    assert not await rig.exists(sid)


async def test_sessions_not_in_the_trash_are_never_purged(rig: Rig) -> None:
    sid = await rig.session()
    await purge_trash(rig.sm, rig.data, rig.outbox, 0, now=NOW + timedelta(days=999))
    assert await rig.exists(sid)


async def test_a_failed_file_removal_keeps_the_row_and_the_next_sweep_finishes(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = await rig.session(deleted_days_ago=40)
    real = shutil.rmtree

    def boom(*a: Any, **k: Any) -> None:
        raise OSError("disk on fire")

    monkeypatch.setattr(shutil, "rmtree", boom)
    assert await purge_trash(rig.sm, rig.data, rig.outbox, 30, now=NOW) == (0, 1)
    assert await rig.exists(sid) and await rig.rows(SegmentRow, sid) == 1  # nothing half-deleted
    monkeypatch.setattr(shutil, "rmtree", real)
    assert await purge_trash(rig.sm, rig.data, rig.outbox, 30, now=NOW) == (1, 0)
    assert not await rig.exists(sid) and not rig.audio(sid).exists()


async def test_a_session_already_missing_its_files_still_purges(rig: Rig) -> None:
    sid = await rig.session(deleted_days_ago=40)
    shutil.rmtree(rig.audio(sid))
    rig.note(sid).unlink()
    assert (await purge_trash(rig.sm, rig.data, rig.outbox, 30, now=NOW))[0] == 1


# --- delete after N days ----------------------------------------------------------------------


async def test_a_due_delete_after_moves_the_session_to_the_trash_not_away(rig: Rig) -> None:
    due = await rig.session(purge_in_days=-1)
    later = await rig.session(purge_in_days=5)
    n = await soft_delete_due(rig.sm, now=NOW)
    assert n == 1
    async with rig.sm() as db:
        first, second = await db.get(SessionRow, due), await db.get(SessionRow, later)
        assert first is not None and second is not None
        assert first.deleted_at == NOW and second.deleted_at is None
    assert rig.audio(due).is_dir()  # still recoverable


async def test_delete_after_never_hits_a_session_still_recording_or_already_deleted(
    rig: Rig,
) -> None:
    live = await rig.session(purge_in_days=-1, status="live")
    gone = await rig.session(purge_in_days=-1, deleted_days_ago=3)
    assert await soft_delete_due(rig.sm, now=NOW) == 0
    async with rig.sm() as db:
        a, b = await db.get(SessionRow, live), await db.get(SessionRow, gone)
        assert a is not None and b is not None
        assert a.deleted_at is None and b.deleted_at == NOW - timedelta(days=3)


# --- the whole sweep ---------------------------------------------------------------------------


async def test_a_sweep_runs_all_three_and_a_fresh_delete_does_not_purge_at_once(rig: Rig) -> None:
    pcm_session = await rig.session()
    old_pcm = rig.audio(pcm_session) / "mic.pcm"
    import os

    os.utime(old_pcm, ((NOW - timedelta(days=100)).timestamp(),) * 2)
    trash = await rig.session(deleted_days_ago=45)
    due = await rig.session(purge_in_days=-1)
    result = await sweep(rig.sm, rig.data, rig.outbox, audio_days=90, trash_days=30, now=NOW)
    assert (result.audio_files, result.soft_deleted, result.purged, result.failed) == (1, 1, 1, 0)
    assert not old_pcm.exists() and await rig.exists(pcm_session)  # rows kept, as before
    assert not await rig.exists(trash)
    assert await rig.exists(due) and rig.audio(due).is_dir()  # in the trash, with its audio


async def test_the_audio_purge_and_the_trash_window_are_separate_settings() -> None:
    s = Settings()
    assert (s.audio_retention_days, s.trash_retention_days) == (90, 30)
    assert Settings(trash_retention_days=7).audio_retention_days == 90


# --- the loop -----------------------------------------------------------------------------------


async def test_a_failing_sweep_is_reported_once_per_run_and_the_loop_survives(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jarvis_live.retention as r

    calls = 0

    async def broken(*a: Any, **k: Any) -> Any:
        nonlocal calls
        calls += 1
        raise RuntimeError("db gone")

    monkeypatch.setattr(r, "sweep", broken)
    notifier = FakeNotifier()
    task = asyncio.create_task(
        run_retention_loop(
            rig.sm, rig.data, 90, 0.01, trash_days=30, outbox=rig.outbox, notifier=notifier
        )
    )
    for _ in range(200):
        if calls >= 3:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert calls >= 3  # still looping
    assert notifier.sent and notifier.sent[0].priority == 8
    assert "retention" in notifier.sent[0].title.lower() and "db gone" in notifier.sent[0].message


async def test_a_purge_that_fails_for_one_session_is_reported(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    await rig.session(deleted_days_ago=40)
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    notifier = FakeNotifier()
    task = asyncio.create_task(
        run_retention_loop(
            rig.sm, rig.data, 90, 3600, trash_days=0, outbox=rig.outbox, notifier=notifier
        )
    )
    for _ in range(200):
        if notifier.sent:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert notifier.sent and "1" in notifier.sent[0].message
