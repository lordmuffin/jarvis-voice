"""Backups of the database and of what the recordings and notes are, and restoring one session
from them.

``jarvis-live backup`` (a CronJob) writes, into the backup directory:

* ``db/<stamp>.dump``: ``pg_dump -Fc`` of the database, checked by reading it back;
* ``audio/<stamp>.manifest.json``: every recording (``sessions/<id>/*.pcm``) and note with its
  size and sha256. The audio bytes themselves are kept offsite by Velero (the data volume is in
  the namespace it backs up); the manifest is what ties a database dump to a copy of the audio;
* ``last_success.json``, which the server reads to notice a backup that stopped happening.

``restore_session`` brings one session back from a dump and a copy of the audio into a target
database and directory, verifying every recording against the manifest first. It never writes a
session's rows without its audio.
"""

import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Table, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from jarvis_live.db.models import (
    AudioChunk,
    Base,
    CopilotItem,
    Device,
    MarkerRow,
    SegmentRow,
    User,
)
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.finalize.outbox import FINAL_NOTE_KIND, note_file
from jarvis_live.notify.gotify import Notifier

log = logging.getLogger(__name__)

_STAMP = "%Y%m%dT%H%M%SZ"
_READ = 1 << 20


class BackupError(Exception):
    """A backup did not complete; the message says why (it goes into the Gotify notice)."""


class RestoreError(Exception):
    """A restore was refused or failed; nothing was written."""


@dataclass(frozen=True)
class PgTools:
    """How to run ``pg_dump`` and ``pg_restore`` (a command prefix, so a test can run them in a
    container). Both read and write through stdin/stdout, never named files."""

    dump: tuple[str, ...] = ("pg_dump",)
    restore: tuple[str, ...] = ("pg_restore",)


@dataclass(frozen=True)
class BackupResult:
    dump: Path
    manifest: Path
    files: int
    bytes: int
    pruned: list[Path] = field(default_factory=list)


def libpq_url(url: str) -> str:
    """The app's SQLAlchemy URL as a libpq one."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _sha256_prefix(path: Path, size: int) -> str:
    """sha256 of the first ``size`` bytes: a recording that is still growing is hashed at the
    length the manifest records, so a later copy that is longer still verifies."""
    h = hashlib.sha256()
    left = size
    with path.open("rb") as f:
        while left > 0:
            chunk = f.read(min(_READ, left))
            if not chunk:
                break
            h.update(chunk)
            left -= len(chunk)
    return h.hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# --- the manifest -------------------------------------------------------------------------------


def _previous_manifest(audio_dir: Path) -> dict[str, Any] | None:
    for path in sorted(audio_dir.glob("*.manifest.json"), reverse=True):
        try:
            data: dict[str, Any] = json.loads(path.read_text())
            return data
        except (OSError, ValueError):
            continue
    return None


def build_manifest(
    data_dir: Path, outbox_dir: Path, previous: dict[str, Any] | None, now: datetime
) -> dict[str, Any]:
    """Every recording and note with its size and hash. A file whose size and mtime are those of
    the previous manifest is not read again."""
    known: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, key in (("audio", "name"), ("outbox", "path")):
        for e in (previous or {}).get(kind, []):
            known[(kind, f"{e.get('session_id', '')}/{e[key]}")] = e

    def entry(kind: str, ident: str, path: Path, extra: dict[str, Any]) -> dict[str, Any]:
        st = path.stat()
        old = known.get((kind, ident))
        if old and old.get("size") == st.st_size and old.get("mtime_ns") == st.st_mtime_ns:
            digest = old["sha256"]
        else:
            digest = _sha256_prefix(path, st.st_size)
        return {**extra, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": digest}

    audio: list[dict[str, Any]] = []
    sessions = data_dir / "sessions"
    if sessions.is_dir():
        for sdir in sorted(p for p in sessions.iterdir() if p.is_dir()):
            for f in sorted(sdir.glob("*.pcm")):
                audio.append(
                    entry(
                        "audio",
                        f"{sdir.name}/{f.name}",
                        f,
                        {"session_id": sdir.name, "name": f.name},
                    )
                )
    notes: list[dict[str, Any]] = []
    if outbox_dir.is_dir():
        for f in sorted(p for p in outbox_dir.rglob("*.md") if p.is_file()):
            rel = f.relative_to(outbox_dir).as_posix()
            if note_file(outbox_dir, rel) is not None:
                notes.append(entry("outbox", f"/{rel}", f, {"path": rel}))
    return {"version": 1, "created_at": now.isoformat(), "audio": audio, "outbox": notes}


# --- running pg_dump / pg_restore ---------------------------------------------------------------


def _run(cmd: list[str], *, stdin: Path | None = None, stdout: Path | None = None) -> bytes:
    try:
        with (
            stdin.open("rb") if stdin else open(os.devnull, "rb") as fin,
            stdout.open("wb") if stdout else open(os.devnull, "wb") as fout,
        ):
            proc = subprocess.run(  # noqa: S603  (a fixed command prefix from settings, no shell)
                cmd, stdin=fin, stdout=fout, stderr=subprocess.PIPE, check=False
            )
    except FileNotFoundError as e:
        raise BackupError(f"could not run {cmd[0]}: {e}") from e
    if proc.returncode != 0:
        tail = proc.stderr.decode(errors="replace")[-500:]
        raise BackupError(f"{Path(cmd[0]).name} exited {proc.returncode}: {tail}")
    return proc.stderr


def _pg_dump(tools: PgTools, database_url: str, out: Path) -> None:
    _run(
        [
            *tools.dump,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            libpq_url(database_url),
        ],
        stdout=out,
    )
    if out.stat().st_size == 0:
        raise BackupError("pg_dump wrote nothing")


def _pg_list(tools: PgTools, dump: Path) -> None:
    try:
        _run([*tools.restore, "--list"], stdin=dump)
    except BackupError as e:
        raise BackupError(f"the dump cannot be read back: {e}") from e


# --- backing up ---------------------------------------------------------------------------------


def run_backup(
    *,
    database_url: str,
    data_dir: Path,
    outbox_dir: Path,
    backup_dir: Path,
    tools: PgTools = PgTools(),  # noqa: B008
    now: datetime | None = None,
    keep_daily: int = 7,
    keep_weekly: int = 4,
) -> BackupResult:
    now = now or datetime.now(UTC)
    stamp = now.strftime(_STAMP)
    db_dir, audio_dir = backup_dir / "db", backup_dir / "audio"
    try:
        db_dir.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise BackupError(f"the backup directory is not writable: {e}") from e
    dump, part = db_dir / f"{stamp}.dump", db_dir / f"{stamp}.dump.part"
    manifest_path = audio_dir / f"{stamp}.manifest.json"
    try:
        _pg_dump(tools, database_url, part)
        _pg_list(tools, part)
        os.replace(part, dump)
        manifest = build_manifest(data_dir, outbox_dir, _previous_manifest(audio_dir), now)
        _atomic_write(manifest_path, json.dumps(manifest))
    except BaseException:
        part.unlink(missing_ok=True)
        dump.unlink(missing_ok=True)  # a dump without its manifest is half a backup
        manifest_path.unlink(missing_ok=True)
        raise
    files = len(manifest["audio"]) + len(manifest["outbox"])
    total = sum(e["size"] for e in manifest["audio"] + manifest["outbox"]) + dump.stat().st_size
    _atomic_write(
        backup_dir / "last_success.json",
        json.dumps(
            {
                "finished_at": now.isoformat(),
                "dump": dump.name,
                "manifest": manifest_path.name,
                "files": files,
                "bytes": total,
            }
        ),
    )
    try:
        pruned = prune(backup_dir, keep_daily=keep_daily, keep_weekly=keep_weekly)
    except OSError as e:
        raise BackupError(f"the backup was made but pruning old ones failed: {e}") from e
    return BackupResult(dump, manifest_path, files, total, pruned)


def prune(backup_dir: Path, *, keep_daily: int, keep_weekly: int) -> list[Path]:
    """Keep the newest backup of each of the last ``keep_daily`` days and of each of the last
    ``keep_weekly`` weeks; delete the rest (dump and manifest together)."""
    stamps = sorted(
        (p.name.removesuffix(".dump") for p in (backup_dir / "db").glob("*.dump")), reverse=True
    )
    keep: set[str] = set(stamps[:1])  # never the only one

    def newest_per(key: Callable[[datetime], object], limit: int) -> None:
        seen: list[object] = []
        for stamp in stamps:  # newest first: the first of each group is its newest
            k = key(datetime.strptime(stamp, _STAMP))
            if k not in seen:
                if len(seen) >= limit:
                    return
                seen.append(k)
                keep.add(stamp)

    newest_per(lambda d: d.date(), keep_daily)
    newest_per(lambda d: d.isocalendar()[:2], keep_weekly)
    removed: list[Path] = []
    for stamp in stamps:
        if stamp in keep:
            continue
        for path in (
            backup_dir / "db" / f"{stamp}.dump",
            backup_dir / "audio" / f"{stamp}.manifest.json",
        ):
            if path.exists():
                path.unlink()
                removed.append(path)
    return removed


@dataclass(frozen=True)
class BackupStatus:
    last_success_at: datetime | None
    age_s: float | None
    stale: bool


def backup_status(backup_dir: Path, *, now: datetime, max_age_s: float) -> BackupStatus:
    """How long since the last good backup. With none on record, the volume's age stands in for
    it, so a job that never ran is noticed once it has had ``max_age_s`` to."""
    if not backup_dir.is_dir():
        return BackupStatus(None, None, False)
    try:
        at = datetime.fromisoformat(
            json.loads((backup_dir / "last_success.json").read_text())["finished_at"]
        )
    except (OSError, ValueError, KeyError):
        since = datetime.fromtimestamp(backup_dir.stat().st_mtime, UTC)
        return BackupStatus(None, None, (now - since).total_seconds() > max_age_s)
    age = (now - at).total_seconds()
    return BackupStatus(at, age, age > max_age_s)


async def run_and_report(
    *,
    database_url: str,
    data_dir: Path,
    outbox_dir: Path,
    backup_dir: Path,
    notifier: Notifier,
    tools: PgTools = PgTools(),  # noqa: B008
    now: datetime | None = None,
    keep_daily: int = 7,
    keep_weekly: int = 4,
) -> int:
    """Back up; on any failure, say so on Gotify. Returns the process exit code."""
    try:
        result = await asyncio.to_thread(
            run_backup,
            database_url=database_url,
            data_dir=data_dir,
            outbox_dir=outbox_dir,
            backup_dir=backup_dir,
            tools=tools,
            now=now,
            keep_daily=keep_daily,
            keep_weekly=keep_weekly,
        )
    except Exception as e:
        log.exception("backup failed")
        reason = str(e) or e.__class__.__name__
        await notifier.notify(
            "Jarvis backup failed", f"The nightly backup did not complete: {reason}", 8
        )
        return 1
    log.info("backup %s: %d files, %d bytes", result.dump.name, result.files, result.bytes)
    return 0


# --- restoring one session ----------------------------------------------------------------------

# Child tables in insert order (each row set is read by ``session_id``).
_BY_SESSION = (
    "audio_chunks",
    "segments",
    "markers",
    "copilot_items",
    "note_publications",
    "work_items",
)
_USER_COLUMNS = {
    "sessions": ("user_id",),
    "note_publications": ("published_by",),
    "work_items": ("user_id", "approved_by"),
    "work_item_dispatches": ("requested_by",),
    "work_item_events": ("actor_user_id",),
}
_SERIAL = (
    ("audio_chunks", "id"),
    ("markers", "id"),
    ("copilot_items", "id"),
    ("work_item_events", "id"),
)


@dataclass
class RestoreReport:
    session_id: uuid.UUID
    rows: dict[str, int] = field(default_factory=dict)
    audio_files: int = 0
    notes: int = 0
    mismatches: list[str] = field(default_factory=list)


def _verify_audio(manifest: dict[str, Any], sid: uuid.UUID, audio_src: Path) -> list[Path]:
    """The session's recordings in ``audio_src``, each checked against the manifest; raises
    ``RestoreError`` naming every file that is missing, short or different."""
    problems: list[str] = []
    found: list[Path] = []
    for e in manifest.get("audio", []):
        if e["session_id"] != str(sid):
            continue
        path = audio_src / "sessions" / str(sid) / e["name"]
        try:
            size = path.stat().st_size
        except OSError:
            problems.append(f"{e['name']}: missing")
            continue
        if size < e["size"]:
            problems.append(f"{e['name']}: {size} bytes, the backup recorded {e['size']}")
        elif _sha256_prefix(path, e["size"]) != e["sha256"]:
            problems.append(f"{e['name']}: contents differ from the backup")
        else:
            found.append(path)
    if problems:
        raise RestoreError("recording does not match the backup: " + "; ".join(problems))
    return found


async def _rows(conn: AsyncConnection, table: Table, where: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in (await conn.execute(select(table).where(where))).mappings().all()]


async def restore_session(
    session_id: uuid.UUID,
    *,
    dump: Path,
    manifest: Path,
    audio_src: Path,
    outbox_src: Path,
    scratch_url: str,
    target_url: str,
    target_data: Path,
    target_outbox: Path,
    tools: PgTools = PgTools(),  # noqa: B008
    force: bool = False,
) -> RestoreReport:
    """Bring one session back: rows from ``dump`` (loaded into ``scratch_url``, a database that
    may be overwritten), recordings from ``audio_src`` (a copy of the data volume), into
    ``target_url`` / ``target_data`` / ``target_outbox``. Refuses to replace an existing session
    unless ``force``."""
    try:
        info = json.loads(manifest.read_text())
    except (OSError, ValueError) as e:
        raise RestoreError(f"the manifest cannot be read: {e}") from e
    try:
        await asyncio.to_thread(
            _run,
            [
                *tools.restore,
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
                "--dbname",
                libpq_url(scratch_url),
            ],
            stdin=dump,
        )
    except BackupError as e:
        raise RestoreError(f"the dump could not be loaded: {e}") from e

    tables = Base.metadata.tables
    src_engine, dst_engine = create_async_engine(scratch_url), create_async_engine(target_url)
    staged: Path | None = None
    try:
        async with src_engine.connect() as src:
            if not await _rows(src, tables["sessions"], SessionRow.id == session_id):
                raise RestoreError(f"session {session_id} is not in the backup")
            audio_files = await asyncio.to_thread(_verify_audio, info, session_id, audio_src)

            session_rows = await _rows(src, tables["sessions"], SessionRow.id == session_id)
            children: dict[str, list[dict[str, Any]]] = {}
            for name in _BY_SESSION:
                t = tables[name]
                children[name] = await _rows(src, t, t.c.session_id == session_id)
            item_ids = [r["id"] for r in children["work_items"]]
            if item_ids:
                events, dispatches = tables["work_item_events"], tables["work_item_dispatches"]
                children["work_item_events"] = await _rows(
                    src, events, events.c.work_item_id.in_(item_ids)
                )
                children["work_item_dispatches"] = await _rows(
                    src, dispatches, dispatches.c.work_item_id.in_(item_ids)
                )
            user_ids: set[Any] = set()
            for name, rows in {"sessions": session_rows, **children}.items():
                for col in _USER_COLUMNS.get(name, ()):
                    user_ids |= {r[col] for r in rows if r[col] is not None}
            device_ids = {r["device_id"] for r in session_rows if r["device_id"] is not None}
            users = await _rows(src, tables["users"], User.id.in_(user_ids)) if user_ids else []
            devices = (
                await _rows(src, tables["devices"], Device.id.in_(device_ids)) if device_ids else []
            )
            note_names = [
                str(r["payload"]["path"])
                for r in children["copilot_items"]
                if r["kind"] == FINAL_NOTE_KIND
            ]

        final = target_data / "sessions" / str(session_id)
        async with dst_engine.begin() as dst:
            exists = await dst.scalar(select(SessionRow.id).where(SessionRow.id == session_id))
            if exists is not None:
                if not force:
                    raise RestoreError(
                        f"session {session_id} already exists in the target; "
                        "use force to replace it"
                    )
                await dst.execute(
                    tables["audio_chunks"].delete().where(AudioChunk.session_id == session_id)
                )
                await dst.execute(
                    tables["segments"].delete().where(SegmentRow.session_id == session_id)
                )
                await dst.execute(
                    tables["markers"].delete().where(MarkerRow.session_id == session_id)
                )
                await dst.execute(
                    tables["copilot_items"].delete().where(CopilotItem.session_id == session_id)
                )
                await dst.execute(
                    tables["sessions"].delete().where(SessionRow.id == session_id)
                )  # cascades
            # Stage the files first: if anything below fails, nothing is left behind.
            staged = target_data / "sessions" / f".{session_id}.restoring-{uuid.uuid4().hex[:6]}"
            await asyncio.to_thread(staged.mkdir, parents=True)
            for path in audio_files:
                await asyncio.to_thread(shutil.copy2, path, staged / path.name)
            try:
                if users:
                    await dst.execute(
                        insert(tables["users"])
                        .values(users)
                        .on_conflict_do_nothing(index_elements=["id"])
                    )
                if devices:
                    await dst.execute(
                        insert(tables["devices"])
                        .values(devices)
                        .on_conflict_do_nothing(index_elements=["id"])
                    )
                await dst.execute(insert(tables["sessions"]).values(session_rows))
                for name in (*_BY_SESSION, "work_item_events", "work_item_dispatches"):
                    if children.get(name):
                        await dst.execute(insert(tables[name]).values(children[name]))
                for table, col in _SERIAL:  # new rows must not collide with the restored ids
                    await dst.execute(
                        text(
                            f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), "
                            f"GREATEST((SELECT COALESCE(max({col}), 1) FROM {table}), 1))"
                        )
                    )
            except SQLAlchemyError as e:
                raise RestoreError(
                    f"the rows could not be restored: {e.__class__.__name__}: {e}"
                ) from e
        # Rows are committed: put the files where the app looks for them.
        if final.exists():
            await asyncio.to_thread(shutil.rmtree, final)
        await asyncio.to_thread(os.replace, staged, final)
        staged = None
        notes = 0
        for name in note_names:
            src_note, dst_note = note_file(outbox_src, name), note_file(target_outbox, name)
            if src_note is not None and dst_note is not None and src_note.is_file():
                await asyncio.to_thread(dst_note.parent.mkdir, parents=True, exist_ok=True)
                await asyncio.to_thread(shutil.copy2, src_note, dst_note)
                notes += 1
        report = RestoreReport(session_id, audio_files=len(audio_files), notes=notes)
        report.rows = {"sessions": 1, **{k: len(v) for k, v in children.items() if v}}
        return report
    finally:
        if staged is not None:
            shutil.rmtree(staged, ignore_errors=True)
        await src_engine.dispose()
        await dst_engine.dispose()
