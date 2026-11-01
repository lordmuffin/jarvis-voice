"""The ideas pass: finds work items in a session's transcript and keeps them up to date.

It runs next to the copilot loop, on its own cadence and with a bigger view (every open item of
the session, not an 8-minute window), so a slow or failed pass never delays suggestions. Each
pass sends only the transcript since the last pass (plus some overlap for context), numbered
``L1..Ln``; the LLM attaches lines to existing items or proposes new ones, and ``IdeasState``
decides what is kept. One more pass runs when the session ends, before the note is written."""

import asyncio
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.config import Settings
from jarvis_live.copilot.loop import Clock, RealClock
from jarvis_live.copilot.transcript import load_final_lines
from jarvis_live.db.models import CopilotItem, SegmentRow, WorkItem
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.llm.client import LLM, LLMError
from jarvis_live.logging_setup import session_logger
from jarvis_live.workitems.rows import store_changes, to_item
from jarvis_live.workitems.schema import IdeasDelta
from jarvis_live.workitems.state import CLOSED, OPEN, IdeasState, number_lines

CURSOR = "ideas_cursor"  # copilot_items kind: {"until_ms": end of the last line considered}

IDEAS_SYSTEM = """/no_think
You help the person labelled "me" turn ideas they speak out loud into work items: concrete \
changes to one of their code repositories that an AI coding agent could carry out.

You receive the repositories, the work items found so far (with refs), the titles of items \
already closed, and new transcript lines numbered "L12 [mm:ss] me|them: text".

Reply with JSON only: {"items_upsert": [...]}. Each entry:
- ref: the ref of an existing open item when the new lines add to, refine or correct it. Omit \
it to propose a new item. Related ideas spoken at different times belong in the same item.
- title: a short imperative phrase, under 10 words.
- repo: exactly one of the repository names listed, or null if it is not clear which.
- intent: 1-4 sentences on what to change and why, in the speaker's terms.
- acceptance_criteria: 1-6 short, checkable statements that are true once the work is done.
- done_when: one sentence.
- source_lines: the L refs of the lines where this idea was spoken. Required: never propose \
an item without them.

Rules:
- Only code or configuration changes in a repository are work items. Errands, reminders, \
meeting logistics and decisions about people are not.
- Never repeat or reopen a closed item.
- If the speaker retracts an idea ("scratch that", "never mind"), do not propose it, and do \
not keep it in an item's criteria.
- When refining an existing item, send all its fields as they should now read.
- Use only what was said; do not invent requirements. Transcripts contain speech-to-text errors.
- Return {"items_upsert": []} when the new lines add nothing."""


def ideas_user(
    *, today: str, repos: dict[str, str], open_json: str, closed: list[str], transcript: str
) -> str:
    repo_lines = "\n".join(f"- {n}: {d}" if d else f"- {n}" for n, d in repos.items())
    closed_lines = "\n".join(f"- {t}" for t in closed)
    return (
        f"Today: {today}\n\n"
        f"REPOSITORIES\n{repo_lines or '(none configured: use null)'}\n\n"
        f"OPEN WORK ITEMS\n{open_json}\n\n"
        f"CLOSED ITEMS (do not propose again)\n{closed_lines or '(none)'}\n\n"
        f"NEW TRANSCRIPT\n{transcript}"
    )


def _open_json(rows: list[WorkItem]) -> str:
    return json.dumps(
        [
            {
                "ref": r.ref,
                "title": r.title,
                "repo": r.repo,
                "intent": r.intent,
                "acceptance_criteria": r.acceptance_criteria,
                "heard": [s["text"] for s in r.sources[:3]],
            }
            for r in rows
            if r.state == OPEN
        ],
        ensure_ascii=False,
    )


class IdeasPass:
    """One pass over a session. Safe to call from the loop and from the end-of-session flush;
    the orchestrator never runs two at once for a session."""

    def __init__(
        self, settings: Settings, sessionmaker: "async_sessionmaker[AsyncSession]", llm: LLM
    ) -> None:
        self._cfg = settings
        self._sm = sessionmaker
        self._llm = llm

    async def run(self, session_id: uuid.UUID) -> bool:
        """Returns True if any work item was created or changed."""
        log = session_logger(__name__, session_id)
        cfg = self._cfg
        async with self._sm() as db:
            session = await db.get(SessionRow, session_id)
            if session is None:
                return False
            cursor = await _cursor(db, session_id)
            after = cursor - int(cfg.ideas_overlap_s * 1000) if cursor >= 0 else -1
            lines = await load_final_lines(db, session_id, after)
            rows = list(
                (await db.execute(select(WorkItem).where(WorkItem.session_id == session_id)))
                .scalars()
                .all()
            )
        if not any(x.end_ms > cursor for x in lines):
            return False
        lines = lines[-cfg.ideas_max_lines :]
        transcript, refs = number_lines(lines)
        user = ideas_user(
            today=datetime.now(UTC).date().isoformat(),
            repos=cfg.work_repo_allowlist,
            open_json=_open_json(rows),
            closed=[r.title for r in rows if r.state in CLOSED],
            transcript=transcript,
        )
        try:
            delta = await self._llm.complete_json(
                model=cfg.ideas_model or cfg.copilot_model,
                system=IDEAS_SYSTEM,
                user=user,
                schema=IdeasDelta,
                timeout_s=cfg.ideas_timeout_s,
                think=False,
            )
        except LLMError as e:
            log.warning("ideas pass skipped: %s", e)
            return False  # the cursor stays put, so the next pass sees these lines again

        until = max(x.end_ms for x in lines)
        async with self._sm() as db, db.begin():
            # Re-read under row locks: the owner may have edited an item while the LLM ran.
            current = list(
                (
                    await db.execute(
                        select(WorkItem).where(WorkItem.session_id == session_id).with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            state = IdeasState([to_item(r) for r in current], cfg.work_repo_allowlist)
            changes = state.apply(delta, refs)
            await store_changes(db, session, current, changes)
            db.add(CopilotItem(session_id=session_id, kind=CURSOR, payload={"until_ms": until}))
        if changes:
            log.info("ideas pass: %d work item(s) changed", len(changes))
        return bool(changes)


async def _cursor(db: AsyncSession, session_id: uuid.UUID) -> int:
    payload = (
        await db.execute(
            select(CopilotItem.payload)
            .where(CopilotItem.session_id == session_id, CopilotItem.kind == CURSOR)
            .order_by(CopilotItem.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return int(payload["until_ms"]) if payload else -1


class IdeasLoop:
    """One per eligible live session. A pass runs after ``ideas_min_new_segments`` new final
    segments, or ``ideas_interval_s`` after the first of ≥1 new ones. Passes are awaited inline,
    so they never overlap."""

    def __init__(
        self,
        session_id: uuid.UUID,
        *,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        runner: IdeasPass,
        clock: Clock | None = None,
    ) -> None:
        self.session_id = session_id
        self._cfg = settings
        self._sm = sessionmaker
        self._runner = runner
        self._clock = clock or RealClock()
        self._log = session_logger(__name__, session_id)
        # Final segments accounted for. 0 at start, so a restarted loop runs a pass soon; the
        # cursor, not this count, decides which lines a pass reads.
        self._seen = 0
        self._pending_since: float | None = None

    async def run(self) -> None:
        while True:
            await self._clock.sleep(self._cfg.copilot_poll_s)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._log.exception("ideas tick failed")

    async def tick(self) -> None:
        total = await self._final_count()
        new = total - self._seen
        now = self._clock.monotonic()
        if new > 0 and self._pending_since is None:
            self._pending_since = now
        due = new >= self._cfg.ideas_min_new_segments or (
            new >= 1
            and self._pending_since is not None
            and now - self._pending_since >= self._cfg.ideas_interval_s
        )
        if due:
            self._seen, self._pending_since = total, None
            await self._runner.run(self.session_id)

    async def _final_count(self) -> int:
        async with self._sm() as db:
            return (
                await db.execute(
                    select(func.count())
                    .select_from(SegmentRow)
                    .where(SegmentRow.session_id == self.session_id, SegmentRow.source == "final")
                )
            ).scalar_one()
