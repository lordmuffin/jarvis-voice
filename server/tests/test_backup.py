"""Backups and restoring one session from them.

The headline test is ``test_a_soft_deleted_session_is_restored_from_a_backup``: it deletes a
session for good (the trash purge), then brings it back from a backup into scratch databases and
directories and compares the audio byte for byte.
"""

import asyncio
import hashlib
import json
import os
import shutil
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import func, select

from jarvis_live import backup as bk
from jarvis_live.backup import PgTools, RestoreError
from jarvis_live.db.models import (
    AudioChunk,
    CopilotItem,
    MarkerRow,
    SegmentRow,
    WorkItem,
)
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.db.session import make_engine, make_sessionmaker, run_migrations
from jarvis_live.retention import purge_trash
from jarvis_live.users import create_user
from tests.fakes import FakeNotifier, add_segments, make_session, new_device
from tests.work_item_helpers import SM, seed_item

NOW = datetime(2026, 11, 20, 3, 0, tzinfo=UTC)
PG_IMAGE = os.environ.get("JARVIS_LIVE_TEST_POSTGRES_IMAGE", "postgres:16-alpine")


@pytest.fixture(scope="session")
def pg_tools() -> PgTools:
    """The host's pg_dump/pg_restore when there are some, else the ones in the Postgres image
    (the same major version as the test database, over the host network)."""
    if shutil.which("pg_dump") and shutil.which("pg_restore"):
        return PgTools()
    docker = ("docker", "run", "--rm", "-i", "--network", "host", PG_IMAGE)
    return PgTools(dump=(*docker, "pg_dump"), restore=(*docker, "pg_restore"))


async def _new_database(
    postgres_url: str, migrate: bool
) -> tuple[str, Callable[[], "asyncio.Future[None]"]]:
    dsn = postgres_url.replace("postgresql+asyncpg://", "postgresql://")
    name = f"bk_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = postgres_url.rsplit("/", 1)[0] + f"/{name}"
    if migrate:
        await asyncio.to_thread(run_migrations, url)

    async def drop() -> None:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()

    return url, drop  # type: ignore[return-value]


@pytest.fixture
async def source(postgres_url: str) -> AsyncIterator[str]:
    """A database of its own, so a backup holds only what this test put there."""
    url, drop = await _new_database(postgres_url, migrate=True)
    yield url
    await drop()


@pytest.fixture
async def scratch(postgres_url: str) -> AsyncIterator[str]:
    url, drop = await _new_database(postgres_url, migrate=False)
    yield url
    await drop()


@pytest.fixture
async def target(postgres_url: str) -> AsyncIterator[str]:
    url, drop = await _new_database(postgres_url, migrate=True)
    yield url
    await drop()


class Env:
    """The app's database and disks as production lays them out, plus the backup directory."""

    def __init__(self, url: str, tmp: Path, tools: PgTools) -> None:
        self.url, self.tools = url, tools
        self.data, self.outbox, self.backups = tmp / "data", tmp / "outbox", tmp / "backups"
        self.engine = make_engine(url)
        self.sm: SM = make_sessionmaker(self.engine)

    async def make_session(
        self, *, audio: dict[str, bytes], deleted_days_ago: float | None = None
    ) -> uuid.UUID:
        user = await create_user(self.sm, f"user {uuid.uuid4().hex[:6]}")
        device, _ = await new_device(self.sm, "phone", user_id=user.id)
        sid = await make_session(self.sm, device, status="done")
        await add_segments(self.sm, sid, [(0, 900, "me", f"hello {sid.hex[:6]}")])
        d = self.data / "sessions" / str(sid)
        d.mkdir(parents=True)
        for channel, data in audio.items():
            (d / f"{channel}.pcm").write_bytes(data)
        rel = f"{sid.hex[:6]}.md"
        self.outbox.mkdir(parents=True, exist_ok=True)
        (self.outbox / rel).write_text(f"---\nsession_id: {sid}\n---\n\nnote of {sid}\n")
        async with self.sm() as db:
            from sqlalchemy import update

            db.add(MarkerRow(session_id=sid, t_ms=1, label="marker"))
            db.add(
                AudioChunk(session_id=sid, channel="mic", seq=0, t_ms=0, byte_offset=0, byte_len=8)
            )
            db.add(CopilotItem(session_id=sid, kind="snapshot", payload={"version": 1}))
            db.add(
                CopilotItem(session_id=sid, kind="final_note", payload={"path": rel, "title": "T"})
            )
            if deleted_days_ago is not None:
                await db.execute(
                    update(SessionRow)
                    .where(SessionRow.id == sid)
                    .values(deleted_at=NOW - timedelta(days=deleted_days_ago))
                )
            await db.commit()
        await seed_item(self.sm, sid, user.id)
        return sid

    def backup(self, **kw: object) -> bk.BackupResult:
        return bk.run_backup(
            database_url=self.url,
            data_dir=self.data,
            outbox_dir=self.outbox,
            backup_dir=self.backups,
            tools=self.tools,
            now=kw.pop("now", NOW),  # type: ignore[arg-type]
            **kw,  # type: ignore[arg-type]
        )

    async def close(self) -> None:
        await self.engine.dispose()


@pytest.fixture
async def env(source: str, tmp_path: Path, pg_tools: PgTools) -> AsyncIterator[Env]:
    e = Env(source, tmp_path, pg_tools)
    yield e
    await e.close()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def count(sm: SM, model: type, sid: uuid.UUID) -> int:
    async with sm() as db:
        return int(
            await db.scalar(
                select(func.count()).select_from(model).where(model.session_id == sid)  # type: ignore[attr-defined]
            )
            or 0
        )


# --- the restore this is all for ----------------------------------------------------------------

MIC = os.urandom(48_000)
SYSTEM = os.urandom(32_000)


async def test_a_soft_deleted_session_is_restored_from_a_backup(
    env: Env, scratch: str, target: str, tmp_path: Path, pg_tools: PgTools
) -> None:
    wanted = await env.make_session(audio={"mic": MIC, "system": SYSTEM}, deleted_days_ago=3)
    other = await env.make_session(audio={"mic": os.urandom(1000)})
    result = env.backup()

    # What Velero holds: a copy of the volumes as they were when the backup ran.
    audio_copy, outbox_copy = tmp_path / "velero-data", tmp_path / "velero-outbox"
    shutil.copytree(env.data, audio_copy)
    shutil.copytree(env.outbox, outbox_copy)

    # Disk loss: the trash purge takes the session's rows, audio and note for good.
    purged, failed = await purge_trash(env.sm, env.data, env.outbox, 0, now=NOW)
    assert (purged, failed) == (1, 0)
    assert not (env.data / "sessions" / str(wanted)).exists()
    assert not (env.outbox / f"{wanted.hex[:6]}.md").exists()
    async with env.sm() as db:
        assert await db.get(SessionRow, wanted) is None

    restored_data, restored_outbox = tmp_path / "restored-data", tmp_path / "restored-outbox"
    report = await bk.restore_session(
        wanted,
        dump=result.dump,
        manifest=result.manifest,
        audio_src=audio_copy,
        outbox_src=outbox_copy,
        scratch_url=scratch,
        target_url=target,
        target_data=restored_data,
        target_outbox=restored_outbox,
        tools=pg_tools,
    )

    # The bytes are the ones that were recorded.
    got = restored_data / "sessions" / str(wanted)
    assert (got / "mic.pcm").read_bytes() == MIC
    assert (got / "system.pcm").read_bytes() == SYSTEM
    assert sha(got / "mic.pcm") == hashlib.sha256(MIC).hexdigest()
    assert (restored_outbox / f"{wanted.hex[:6]}.md").read_text().endswith(f"note of {wanted}\n")
    assert report.audio_files == 2 and report.notes == 1 and report.mismatches == []

    # The rows are back, still in the trash, and nothing else came with them.
    sm = make_sessionmaker(make_engine(target))
    async with sm() as db:
        row = await db.get(SessionRow, wanted)
        assert row is not None and row.deleted_at == NOW - timedelta(days=3)
        assert await db.get(SessionRow, other) is None
    assert await count(sm, SegmentRow, wanted) == 1
    assert await count(sm, MarkerRow, wanted) == 1
    assert await count(sm, AudioChunk, wanted) == 1
    assert await count(sm, CopilotItem, wanted) == 2
    assert await count(sm, WorkItem, wanted) == 1
    assert not (restored_data / "sessions" / str(other)).exists()


async def test_restoring_over_an_existing_session_needs_force(
    env: Env, scratch: str, target: str, tmp_path: Path, pg_tools: PgTools
) -> None:
    sid = await env.make_session(audio={"mic": MIC})
    result = env.backup()
    kwargs = dict(
        dump=result.dump,
        manifest=result.manifest,
        audio_src=env.data,
        outbox_src=env.outbox,
        scratch_url=scratch,
        target_data=tmp_path / "t-data",
        target_outbox=tmp_path / "t-outbox",
        tools=pg_tools,
    )
    await bk.restore_session(sid, target_url=target, **kwargs)  # type: ignore[arg-type]
    with pytest.raises(RestoreError, match="already"):
        await bk.restore_session(sid, target_url=target, **kwargs)  # type: ignore[arg-type]
    await bk.restore_session(sid, target_url=target, force=True, **kwargs)  # type: ignore[arg-type]


async def test_a_session_not_in_the_backup_is_refused_and_nothing_is_written(
    env: Env, scratch: str, target: str, tmp_path: Path, pg_tools: PgTools
) -> None:
    await env.make_session(audio={"mic": MIC})
    result = env.backup()
    with pytest.raises(RestoreError, match="not in the backup"):
        await bk.restore_session(
            uuid.uuid4(),
            dump=result.dump,
            manifest=result.manifest,
            audio_src=env.data,
            outbox_src=env.outbox,
            scratch_url=scratch,
            target_url=target,
            target_data=tmp_path / "t-data",
            target_outbox=tmp_path / "t-outbox",
            tools=pg_tools,
        )
    assert not (tmp_path / "t-data").exists()


async def test_audio_that_does_not_match_the_manifest_is_reported_and_not_restored(
    env: Env, scratch: str, target: str, tmp_path: Path, pg_tools: PgTools
) -> None:
    sid = await env.make_session(audio={"mic": MIC})
    result = env.backup()
    damaged = tmp_path / "damaged"
    shutil.copytree(env.data, damaged)
    (damaged / "sessions" / str(sid) / "mic.pcm").write_bytes(MIC[:-1] + b"\x00")
    with pytest.raises(RestoreError, match="mic.pcm"):
        await bk.restore_session(
            sid,
            dump=result.dump,
            manifest=result.manifest,
            audio_src=damaged,
            outbox_src=env.outbox,
            scratch_url=scratch,
            target_url=target,
            target_data=tmp_path / "t-data",
            target_outbox=tmp_path / "t-outbox",
            tools=pg_tools,
        )
    assert not (tmp_path / "t-data" / "sessions" / str(sid)).exists()
    sm = make_sessionmaker(make_engine(target))
    async with sm() as db:
        assert await db.get(SessionRow, sid) is None  # no rows without their audio


async def test_audio_that_kept_growing_after_the_backup_still_verifies(
    env: Env, scratch: str, target: str, tmp_path: Path, pg_tools: PgTools
) -> None:
    sid = await env.make_session(audio={"mic": MIC})
    result = env.backup()
    grown = tmp_path / "grown"
    shutil.copytree(env.data, grown)
    with (grown / "sessions" / str(sid) / "mic.pcm").open("ab") as f:
        f.write(b"\x07" * 4000)  # the recording went on after the manifest was taken
    await bk.restore_session(
        sid,
        dump=result.dump,
        manifest=result.manifest,
        audio_src=grown,
        outbox_src=env.outbox,
        scratch_url=scratch,
        target_url=target,
        target_data=tmp_path / "t-data",
        target_outbox=tmp_path / "t-outbox",
        tools=pg_tools,
    )
    assert (tmp_path / "t-data" / "sessions" / str(sid) / "mic.pcm").read_bytes().startswith(MIC)


async def test_a_corrupted_dump_is_detected_and_nothing_is_written(
    env: Env, scratch: str, target: str, tmp_path: Path, pg_tools: PgTools
) -> None:
    sid = await env.make_session(audio={"mic": MIC})
    result = env.backup()
    result.dump.write_bytes(result.dump.read_bytes()[:200])  # truncated
    with pytest.raises(RestoreError):
        await bk.restore_session(
            sid,
            dump=result.dump,
            manifest=result.manifest,
            audio_src=env.data,
            outbox_src=env.outbox,
            scratch_url=scratch,
            target_url=target,
            target_data=tmp_path / "t-data",
            target_outbox=tmp_path / "t-outbox",
            tools=pg_tools,
        )
    assert not (tmp_path / "t-data").exists()


# --- the backup itself --------------------------------------------------------------------------


async def test_a_backup_holds_a_readable_dump_and_a_manifest_of_every_recording(env: Env) -> None:
    a = await env.make_session(audio={"mic": MIC, "system": SYSTEM})
    b = await env.make_session(audio={"mic": b"\x01" * 10})
    result = env.backup()
    assert result.dump.stat().st_size > 0 and result.dump.parent == env.backups / "db"
    manifest = json.loads(result.manifest.read_text())
    files = {(f["session_id"], f["name"]): f for f in manifest["audio"]}
    assert set(files) == {
        (str(a), "mic.pcm"),
        (str(a), "system.pcm"),
        (str(b), "mic.pcm"),
    }
    assert files[(str(a), "mic.pcm")]["sha256"] == hashlib.sha256(MIC).hexdigest()
    assert files[(str(a), "mic.pcm")]["size"] == len(MIC)
    assert {n["path"] for n in manifest["outbox"]} == {f"{a.hex[:6]}.md", f"{b.hex[:6]}.md"}
    ok = json.loads((env.backups / "last_success.json").read_text())
    assert ok["dump"] == result.dump.name and ok["files"] == 5


async def test_unchanged_files_are_not_read_again_on_the_next_backup(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    await env.make_session(audio={"mic": MIC})
    env.backup(now=NOW)
    hashed: list[Path] = []
    real = bk._sha256_prefix
    monkeypatch.setattr(bk, "_sha256_prefix", lambda p, n: (hashed.append(p), real(p, n))[1])
    new = await env.make_session(audio={"mic": b"\x09" * 100})
    env.backup(now=NOW + timedelta(days=1))
    assert [p.parent.name for p in hashed if p.suffix == ".pcm"] == [str(new)]


async def test_a_growing_file_is_recorded_at_the_size_that_was_hashed(env: Env) -> None:
    sid = await env.make_session(audio={"mic": MIC})
    result = env.backup()
    entry = next(
        f for f in json.loads(result.manifest.read_text())["audio"] if f["session_id"] == str(sid)
    )
    assert entry["size"] == len(MIC)


async def test_an_empty_installation_backs_up_cleanly(env: Env) -> None:
    result = env.backup()
    assert json.loads(result.manifest.read_text())["audio"] == []


# --- failure is loud ----------------------------------------------------------------------------


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


async def run(env: Env, notifier: FakeNotifier, **kw: object) -> int:
    return await bk.run_and_report(
        database_url=env.url,
        data_dir=env.data,
        outbox_dir=env.outbox,
        backup_dir=env.backups,
        notifier=notifier,
        tools=kw.pop("tools", env.tools),  # type: ignore[arg-type]
        now=NOW,
    )


async def test_a_good_backup_is_quiet(env: Env, notifier: FakeNotifier) -> None:
    await env.make_session(audio={"mic": MIC})
    assert await run(env, notifier) == 0
    assert notifier.sent == []


@pytest.mark.parametrize(
    ("why", "tools"),
    [
        ("pg_dump fails", PgTools(dump=("false",))),
        ("pg_dump writes nothing", PgTools(dump=("true",))),
        ("the dump cannot be read back", None),  # restore tool replaced below
    ],
)
async def test_a_failed_backup_notifies_and_leaves_the_last_success_alone(
    env: Env, notifier: FakeNotifier, why: str, tools: PgTools | None
) -> None:
    await env.make_session(audio={"mic": MIC})
    assert await run(env, notifier) == 0  # a good one first
    before = (env.backups / "last_success.json").read_text()
    if tools is None:
        tools = PgTools(dump=env.tools.dump, restore=("false",))
    assert await run(env, notifier, tools=tools) == 1
    (sent,) = notifier.sent
    assert sent.priority == 8 and sent.title == "Jarvis backup failed"
    assert sent.message  # says what went wrong
    assert (env.backups / "last_success.json").read_text() == before, why
    assert not list((env.backups / "db").glob("*.part"))  # no half-written dump left behind


async def test_an_unwritable_backup_directory_notifies(env: Env, notifier: FakeNotifier) -> None:
    env.backups.parent.mkdir(parents=True, exist_ok=True)
    env.backups.write_text("a file where the directory should be")
    assert await run(env, notifier) == 1
    assert notifier.sent and notifier.sent[0].priority == 8


async def test_a_missing_pg_dump_binary_notifies(env: Env, notifier: FakeNotifier) -> None:
    assert await run(env, notifier, tools=PgTools(dump=("/nonexistent/pg_dump",))) == 1
    assert "pg_dump" in notifier.sent[0].message


# --- pruning ------------------------------------------------------------------------------------


def _fake_backup(root: Path, when: datetime) -> str:
    stamp = when.strftime("%Y%m%dT%H%M%SZ")
    (root / "db").mkdir(parents=True, exist_ok=True)
    (root / "audio").mkdir(parents=True, exist_ok=True)
    (root / "db" / f"{stamp}.dump").write_bytes(b"x")
    (root / "audio" / f"{stamp}.manifest.json").write_text("{}")
    return stamp


def test_pruning_keeps_the_last_seven_days_and_the_last_four_weeks(tmp_path: Path) -> None:
    start = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
    stamps = [_fake_backup(tmp_path, start + timedelta(days=d)) for d in range(80)]
    removed = bk.prune(tmp_path, keep_daily=7, keep_weekly=4)
    kept = {p.stem for p in (tmp_path / "db").glob("*.dump")}
    newest = stamps[-7:]
    assert set(newest) <= kept
    # and one per week for the four newest weeks, which are not all in the last seven days
    weeks: dict[str, str] = {}
    for s in stamps:
        d = datetime.strptime(s, "%Y%m%dT%H%M%SZ")
        weeks[f"{d.isocalendar().year}-{d.isocalendar().week}"] = s  # the newest of each week
    expected = set(newest) | set(list(weeks.values())[-4:])
    assert kept == expected
    # the manifest goes with its dump
    assert {
        p.name.removesuffix(".manifest.json") for p in (tmp_path / "audio").glob("*.json")
    } == kept
    assert len(removed) == 2 * (80 - len(kept))


def test_pruning_never_touches_the_only_backup(tmp_path: Path) -> None:
    _fake_backup(tmp_path, NOW)
    assert bk.prune(tmp_path, keep_daily=1, keep_weekly=0) == []


# --- noticing a backup that stopped happening ---------------------------------------------------


def test_backup_age_reads_the_last_success(tmp_path: Path) -> None:
    (tmp_path / "last_success.json").write_text(json.dumps({"finished_at": NOW.isoformat()}))
    fresh = bk.backup_status(tmp_path, now=NOW + timedelta(seconds=50), max_age_s=100)
    assert fresh.age_s == 50 and not fresh.stale
    stale = bk.backup_status(tmp_path, now=NOW + timedelta(seconds=500), max_age_s=100)
    assert stale.stale and stale.last_success_at == NOW


def test_a_backup_that_never_ran_is_stale_only_once_its_directory_is_old_enough(
    tmp_path: Path,
) -> None:
    os.utime(tmp_path, (NOW.timestamp(),) * 2)  # the volume was provisioned at NOW
    soon = bk.backup_status(tmp_path, now=NOW + timedelta(seconds=50), max_age_s=100)
    assert (soon.last_success_at, soon.age_s, soon.stale) == (None, None, False)
    late = bk.backup_status(tmp_path, now=NOW + timedelta(seconds=500), max_age_s=100)
    assert late.last_success_at is None and late.stale


def test_a_missing_backup_directory_is_not_stale(tmp_path: Path) -> None:
    status = bk.backup_status(tmp_path / "nope", now=NOW, max_age_s=100)
    assert not status.stale  # backups are not configured here
