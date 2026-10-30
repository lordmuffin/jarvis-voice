"""End-of-session: full-transcript LLM pass -> Markdown note in the outbox -> ``done``."""

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.copilot.loop import RELATED
from jarvis_live.copilot.prompts import (
    FINAL_SYSTEM,
    MAP_SYSTEM,
    REDUCE_SYSTEM,
    final_user,
    reduce_user,
)
from jarvis_live.copilot.schema import FinalNote
from jarvis_live.copilot.transcript import Line, load_lines, render_lines
from jarvis_live.db.models import OWNER_USER_ID, CopilotItem, Device, User, WorkItem
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.finalize.outbox import (
    FINAL_NOTE_KIND,
    note_file,
    outbox_dir,
    overwrite_atomic,
    owned_by,
)
from jarvis_live.finalize.render import NoteMeta, NoteWorkItem, render_filename, render_note
from jarvis_live.llm.client import LLM
from jarvis_live.logging_setup import session_logger
from jarvis_live.notify.gotify import Notifier
from jarvis_live.protocol import FinalNote as FinalNoteMessage
from jarvis_live.vault.index import NullVault, VaultContext
from jarvis_live.workitems.template import session_url

log = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4
MAX_RELATED = 8


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


def split_windows(lines: list[Line], window_ms: int) -> list[list[Line]]:
    buckets: dict[int, list[Line]] = defaultdict(list)
    for x in lines:
        buckets[x.start_ms // window_ms].append(x)
    return [buckets[k] for k in sorted(buckets)]


@dataclass(frozen=True)
class FinalResult:
    path: str  # relative to the outbox
    title: str


class Finalizer:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        bus: Bus,
        llm: LLM,
        vault: VaultContext,
        notifier: Notifier,
    ) -> None:
        self._cfg = settings
        self._sm = sessionmaker
        self._bus = bus
        self._llm = llm
        self._vault = vault
        self._notifier = notifier

    @property
    def outbox(self) -> Path:
        return outbox_dir(self._cfg)

    async def run(
        self, session_id: uuid.UUID, *, replace: str | None = None, notify: bool = True
    ) -> FinalResult | None:
        """Finalize one session. Never raises: failures set status ``failed`` and notify.

        ``replace`` is the outbox path of an earlier note of this session (a regeneration): if
        the new note lands under a different name, the old file is removed. ``notify=False``
        skips the "note ready" push (a regeneration the user asked for and is watching)."""
        slog = session_logger(__name__, session_id)
        try:
            result = await self._finalize(session_id, replace=replace, notify=notify)
        except asyncio.CancelledError:
            raise  # shutdown: stays ``finalizing`` and is retried on the next start
        except Exception:
            slog.exception("finalizer failed")
            await self._set_status(session_id, "failed")
            await self._notify_owner(
                session_id,
                "Jarvis: note failed",
                f"Could not finalize session {session_id}. Try again from the sessions list.",
                8,
                self._session_url(session_id),
            )
            return None
        return result

    async def _finalize(
        self, session_id: uuid.UUID, *, replace: str | None, notify: bool
    ) -> FinalResult | None:
        async with self._sm() as db:
            row = await db.get(SessionRow, session_id)
            if row is None or row.status != "finalizing":
                return None
            device = await db.get(Device, row.device_id) if row.device_id else None
            user = await db.get(User, row.user_id)
            lines = await load_lines(db, session_id)
            related_names = await self._shown_related(db, session_id)
            work_items = await self._work_items(db, session_id)
        if row.local_only:
            await self._set_status(session_id, "done")  # never leaves the device's control
            return None
        if not lines:
            session_logger(__name__, session_id).info("no transcript; nothing to write")
            await self._set_status(session_id, "done")
            return None

        tz = ZoneInfo(self._cfg.timezone)
        created = row.started_at.astimezone(tz)
        ended = row.ended_at or datetime.now(UTC)
        duration_s = max((ended - row.started_at).total_seconds(), lines[-1].end_ms / 1000)
        today = created.date().isoformat()

        candidates = "\n".join(f"- {n}" for n in related_names)
        note = await self._summarize(row.mode, today, candidates, lines)
        # The vault is the owner's own: other users' notes link nothing from it.
        vault = self._vault if row.user_id == OWNER_USER_ID else NullVault()
        note.related = await self._resolve_related(vault, note.related, related_names)

        meta = NoteMeta(
            created=created,
            session_id=str(session_id),
            device=device.name if device else "unknown",
            user=user.display_name if user else None,
            mode=row.mode,
            source=row.source,
            duration_minutes=max(1, round(duration_s / 60)),
        )
        markdown = render_note(note, meta, lines, work_items)
        name = render_filename(
            self._cfg.final_filename_template, created, note.title, str(session_id)
        )
        # Each user's notes go to their own folder; the owner's (no folder) to the outbox root.
        subdir = user.notes_subdir if user else None
        written = await asyncio.to_thread(
            write_atomic,
            self.outbox / subdir if subdir else self.outbox,
            name,
            markdown,
            str(session_id),
        )
        if subdir:
            written = f"{subdir}/{written}"
        if replace and replace != written:
            await asyncio.to_thread(self._remove_old, replace, str(session_id))

        await self._persist_final(session_id, written, note.title)
        await self._fill_title(session_id, note.title)
        await self._set_status(session_id, "done")
        self._bus.publish(str(session_id), FinalNoteMessage(path=written, title=note.title))
        if notify:
            await self._notify_owner(
                session_id,
                "Jarvis: note ready",
                f"**{note.title}**\n\n{note.summary.strip()}",
                5,
                self._session_url(session_id),
            )
        return FinalResult(written, note.title)

    def _remove_old(self, name: str, session_id: str) -> None:
        """Drop a regenerated session's previous note, but never another session's file."""
        path = note_file(self.outbox, name)
        if path is not None and owned_by(path, session_id):
            path.unlink(missing_ok=True)

    # --- LLM --------------------------------------------------------------------------------

    async def _summarize(
        self, mode: str, today: str, candidates: str, lines: list[Line]
    ) -> FinalNote:
        transcript = render_lines(lines)
        budget = self._cfg.final_max_input_tokens
        if estimate_tokens(transcript) <= budget:
            return await self._call(
                FINAL_SYSTEM,
                final_user(today=today, mode=mode, related=candidates, transcript=transcript),
            )
        windows = split_windows(lines, int(self._cfg.final_window_s * 1000))
        partials: list[FinalNote] = []
        for w in windows:  # sequential: the heavy model is shared
            partials.append(
                await self._call(
                    MAP_SYSTEM,
                    final_user(
                        today=today, mode=mode, related=candidates, transcript=render_lines(w)
                    ),
                )
            )
        merged = json.dumps([p.model_dump() for p in partials], ensure_ascii=False)
        return await self._call(
            REDUCE_SYSTEM,
            reduce_user(today=today, mode=mode, related=candidates, partials_json=merged),
        )

    async def _call(self, system: str, user: str) -> FinalNote:
        return await self._llm.complete_json(
            model=self._cfg.final_model,
            system=system,
            user=user,
            schema=FinalNote,
            timeout_s=self._cfg.final_timeout_s,
            think=self._cfg.final_thinking,
        )

    # --- related ----------------------------------------------------------------------------

    async def _shown_related(self, db: AsyncSession, session_id: uuid.UUID) -> list[str]:
        """Titles of vault notes the copilot surfaced during the session (as file stems)."""
        payloads = (
            await db.execute(
                select(CopilotItem.payload)
                .where(CopilotItem.session_id == session_id, CopilotItem.kind == RELATED)
                .order_by(CopilotItem.id)
            )
        ).scalars()
        stems: dict[str, None] = {}
        for p in payloads:
            for h in p["hits"]:
                stems[Path(h["path"]).stem] = None
        return list(stems)

    async def _resolve_related(
        self, vault: VaultContext, suggested: list[str], shown: list[str]
    ) -> list[str]:
        """Wikilinks only to notes that exist: what the copilot showed, plus any model
        suggestion the vault index can resolve."""
        out: dict[str, None] = dict.fromkeys(shown)
        for name in suggested:
            if stem := await vault.resolve_name(name):
                out[stem] = None
        return list(out)[:MAX_RELATED]

    async def _work_items(self, db: AsyncSession, session_id: uuid.UUID) -> list[NoteWorkItem]:
        """Drafts and approved work items, as the dashboard has them now."""
        rows = (
            await db.execute(
                select(WorkItem)
                .where(WorkItem.session_id == session_id, WorkItem.state.in_(("draft", "approved")))
                .order_by(WorkItem.created_at, WorkItem.ref)
            )
        ).scalars()
        sid = str(session_id)
        return [
            NoteWorkItem(
                title=r.title,
                repo=r.repo,
                state=r.state,
                intent=r.intent,
                criteria=list(r.acceptance_criteria),
                sources=[
                    (int(s["start_ms"]), session_url(self._cfg, sid, int(s["start_ms"])))
                    for s in r.sources
                ],
                review_url=session_url(self._cfg, sid),
                prompt=r.prompt_md if r.state == "approved" else None,
            )
            for r in rows
        ]

    # --- persistence ------------------------------------------------------------------------

    async def _persist_final(self, session_id: uuid.UUID, path: str, title: str) -> None:
        async with self._sm() as db:
            db.add(
                CopilotItem(
                    session_id=session_id,
                    kind=FINAL_NOTE_KIND,
                    payload={"path": path, "title": title},
                )
            )
            await db.commit()

    async def _fill_title(self, session_id: uuid.UUID, title: str) -> None:
        """A session still untitled at the end (e.g. the copilot never ran) takes the note's
        title. A user's or the copilot's title is kept."""
        async with self._sm() as db:
            await db.execute(
                update(SessionRow)
                .where(SessionRow.id == session_id, SessionRow.title.is_(None))
                .values(title=title)
            )
            await db.commit()

    async def _set_status(self, session_id: uuid.UUID, status: str) -> None:
        async with self._sm() as db:
            await db.execute(
                update(SessionRow)
                .where(SessionRow.id == session_id, SessionRow.status == "finalizing")
                .values(status=status)
            )
            await db.commit()

    async def _notify_owner(
        self, session_id: uuid.UUID, title: str, message: str, priority: int, url: str | None
    ) -> None:
        """Gotify reaches the deployment owner's phone, so only their sessions notify: other
        users' note titles and summaries are not the owner's to read."""
        try:
            async with self._sm() as db:
                user_id = await db.scalar(
                    select(SessionRow.user_id).where(SessionRow.id == session_id)
                )
        except Exception:  # e.g. the database outage that failed the note: tell the owner
            user_id = OWNER_USER_ID
        if user_id == OWNER_USER_ID:
            await self._notifier.notify(title, message, priority, url)

    def _session_url(self, session_id: uuid.UUID) -> str | None:
        base = self._cfg.public_base_url.rstrip("/")
        return f"{base}/sessions/{session_id}" if base else None


def write_atomic(outbox: Path, name: str, content: str, session_id: str) -> str:
    """Write ``content`` to ``outbox/name`` via tmp + rename; returns the final file name.

    If a different session already owns that name, a numeric suffix is added. Re-finalizing the
    same session overwrites its own earlier note."""
    outbox.mkdir(parents=True, exist_ok=True)
    stem, suffix = Path(name).stem, Path(name).suffix
    target = outbox / name
    n = 1
    while target.exists() and not owned_by(target, session_id):
        n += 1
        target = outbox / f"{stem} ({n}){suffix}"
    overwrite_atomic(target, content)
    return target.name
