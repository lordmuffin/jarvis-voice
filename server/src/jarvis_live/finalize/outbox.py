"""The outbox: where finished notes live on disk, and which note belongs to a session.

A note's path is relative to the outbox: a file name, or ``<notes_subdir>/<file name>`` for a
user other than the owner. The latest ``final_note`` copilot item of a session records it."""

import os
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.config import Settings
from jarvis_live.db.models import CopilotItem

FINAL_NOTE_KIND = "final_note"


def outbox_dir(cfg: Settings) -> Path:
    return cfg.outbox_dir or cfg.data_dir / "outbox"


def note_file(outbox: Path, name: str) -> Path | None:
    """``outbox/name`` if ``name`` has the shape the finalizer writes; otherwise None."""
    parts = Path(name).parts
    if not name or not 1 <= len(parts) <= 2 or any(p in {"", ".", ".."} for p in parts):
        return None
    if Path(name).is_absolute():
        return None
    return outbox / name


def read_note(outbox: Path, name: str) -> str | None:
    path = note_file(outbox, name)
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


async def latest_final_note(db: AsyncSession, session_id: uuid.UUID) -> tuple[str, str] | None:
    """``(path, title)`` of the session's current note, if one was written."""
    payload = (
        await db.execute(
            select(CopilotItem.payload)
            .where(CopilotItem.session_id == session_id, CopilotItem.kind == FINAL_NOTE_KIND)
            .order_by(CopilotItem.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if payload is None:
        return None
    return str(payload["path"]), str(payload["title"])


async def record_final_note(
    sm: "async_sessionmaker[AsyncSession]", session_id: uuid.UUID, path: str, title: str
) -> None:
    """Make ``path`` the session's current note."""
    async with sm() as db:
        db.add(
            CopilotItem(
                session_id=session_id,
                kind=FINAL_NOTE_KIND,
                payload={"path": path, "title": title},
            )
        )
        await db.commit()


def overwrite_atomic(target: Path, content: str) -> None:
    """Replace ``target`` with ``content`` via tmp + fsync + rename: readers never see a torn
    file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def owned_by(path: Path, session_id: str) -> bool:
    """The note at ``path`` was written for ``session_id`` (its frontmatter says so)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return f"session_id: {session_id}" in text


def remove_superseded(outbox: Path, name: str, session_id: str) -> None:
    """Drop a session's previous note, but never another session's file."""
    path = note_file(outbox, name)
    if path is not None and owned_by(path, session_id):
        path.unlink(missing_ok=True)
