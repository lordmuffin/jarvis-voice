"""End-of-session: full-transcript LLM pass -> Markdown note in the outbox -> ``done``."""

import asyncio
import json
import logging
import os
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
from jarvis_live.db.models import CopilotItem, Device
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.finalize.render import NoteMeta, render_filename, render_note
from jarvis_live.llm.client import LLM
from jarvis_live.logging_setup import session_logger
from jarvis_live.notify.gotify import Notifier
from jarvis_live.protocol import FinalNote as FinalNoteMessage
from jarvis_live.vault.index import VaultContext

log = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4
MAX_RELATED = 8
FINAL_NOTE_KIND = "final_note"


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
        return self._cfg.outbox_dir or self._cfg.data_dir / "outbox"

    async def run(self, session_id: uuid.UUID) -> FinalResult | None:
        """Finalize one session. Never raises: failures set status ``failed`` and notify."""
        slog = session_logger(__name__, session_id)
        try:
            result = await self._finalize(session_id)
        except asyncio.CancelledError:
            raise  # shutdown: stays ``finalizing`` and is retried on the next start
        except Exception as e:
            slog.exception("finalizer failed")
            await self._set_status(session_id, "failed")
            await self._notifier.notify(
                "Jarvis: note failed",
                f"Could not finalize session `{session_id}`: {e!r}",
                8,
                self._session_url(session_id),
            )
            return None
        return result

    async def _finalize(self, session_id: uuid.UUID) -> FinalResult | None:
        async with self._sm() as db:
            row = await db.get(SessionRow, session_id)
            if row is None or row.status != "finalizing":
                return None
            device = await db.get(Device, row.device_id)
            lines = await load_lines(db, session_id)
            related_names = await self._shown_related(db, session_id)
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
        note.related = await self._resolve_related(note.related, related_names)

        meta = NoteMeta(
            created=created,
            session_id=str(session_id),
            device=device.name if device else "unknown",
            mode=row.mode,
            duration_minutes=max(1, round(duration_s / 60)),
        )
        markdown = render_note(note, meta, lines)
        name = render_filename(
            self._cfg.final_filename_template, created, note.title, str(session_id)
        )
        written = await asyncio.to_thread(
            write_atomic, self.outbox, name, markdown, str(session_id)
        )

        await self._persist_final(session_id, written, note.title)
        await self._set_status(session_id, "done")
        self._bus.publish(str(session_id), FinalNoteMessage(path=written, title=note.title))
        await self._notifier.notify(
            "Jarvis: note ready",
            f"**{note.title}**\n\n{note.summary.strip()}",
            5,
            self._session_url(session_id),
        )
        return FinalResult(written, note.title)

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

    async def _resolve_related(self, suggested: list[str], shown: list[str]) -> list[str]:
        """Wikilinks only to notes that exist: what the copilot showed, plus any model
        suggestion the vault index can resolve."""
        out: dict[str, None] = dict.fromkeys(shown)
        for name in suggested:
            if stem := await self._vault.resolve_name(name):
                out[stem] = None
        return list(out)[:MAX_RELATED]

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

    async def _set_status(self, session_id: uuid.UUID, status: str) -> None:
        async with self._sm() as db:
            await db.execute(
                update(SessionRow)
                .where(SessionRow.id == session_id, SessionRow.status == "finalizing")
                .values(status=status)
            )
            await db.commit()

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
    while target.exists() and f"session_id: {session_id}" not in target.read_text(
        encoding="utf-8", errors="replace"
    ):
        n += 1
        target = outbox / f"{stem} ({n}){suffix}"
    tmp = outbox / f".{target.name}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target.name
