"""Publishing finished notes to the vault repo: one branch and one PR per session.

The same flow as capture's vault-writer (branch + PR against ``main``, never a direct push),
driven from the dashboard: publish, edit while the PR is open, merge, close, skip. Only the
deployment owner and admins publish, and only their own sessions' notes: other users' notes are
not the owner's vault's to hold.

Every change to one session's note or publication runs under that session's lock, so a merge
cannot race an edit. In-process locks suffice: the server runs as a single replica.
"""

import asyncio
import hashlib
import logging
import time
import uuid
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.config import Settings
from jarvis_live.db.models import OWNER_USER_ID, CopilotItem, NotePublication, User
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.finalize.outbox import (
    FINAL_NOTE_KIND,
    latest_final_note,
    note_file,
    outbox_dir,
    overwrite_atomic,
    read_note,
)
from jarvis_live.publish.github import GitHubClient, GitHubError, PullRequest

log = logging.getLogger(__name__)

MAX_NOTE_BYTES = 1_000_000
REFRESH_MIN_INTERVAL_S = 30.0
PUBLISH_ALL_GAP_S = 1.0  # GitHub's secondary limit is ~80 content-creating calls a minute


class PublishError(Exception):
    """A refused or failed publishing action; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def may_publish_for(user: User) -> bool:
    """Whose notes go to the vault: the owner's and admins'."""
    return user.id == OWNER_USER_ID or user.is_admin


@dataclass(frozen=True)
class QueueItem:
    session_id: uuid.UUID
    title: str
    started_at: datetime
    owner: str
    note_path: str
    publication: NotePublication | None


@dataclass(frozen=True)
class Queue:
    ready: list[QueueItem]
    open: list[QueueItem]
    recent: list[QueueItem]


@dataclass(frozen=True)
class _Note:
    session: SessionRow
    owner: User
    path: str  # relative to the outbox
    title: str
    markdown: str


class PublishService:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        github: GitHubClient | None,
    ) -> None:
        self._cfg = settings
        self._sm = sessionmaker
        self._gh = github if settings.publish_enabled else None
        self._locks: weakref.WeakValueDictionary[uuid.UUID, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._refreshed: dict[uuid.UUID, float] = {}
        self._publish_all: asyncio.Task[None] | None = None

    # --- who and when -----------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._gh is not None

    @property
    def repo(self) -> str:
        return self._cfg.publish_repo

    def can_publish(self, actor: User) -> bool:
        return self.enabled and may_publish_for(actor)

    def lock(self, session_id: uuid.UUID) -> asyncio.Lock:
        """The lock every change to this session's note and publication runs under. Hold the
        returned object while using it: entries vanish once nobody does."""
        lock = self._locks.get(session_id)
        if lock is None:
            lock = self._locks[session_id] = asyncio.Lock()
        return lock

    @property
    def publishing_all(self) -> bool:
        return self._publish_all is not None and not self._publish_all.done()

    def _github(self) -> GitHubClient:
        if self._gh is None:
            raise PublishError(503, "publishing is not configured")
        return self._gh

    def _check_actor(self, actor: User) -> GitHubClient:
        gh = self._github()
        if not may_publish_for(actor):
            raise PublishError(403, "only the owner and admins publish notes")
        return gh

    # --- loading ----------------------------------------------------------------------------

    async def _load(self, db: AsyncSession, session_id: uuid.UUID) -> _Note:
        found = (
            await db.execute(
                select(SessionRow, User)
                .join(User, User.id == SessionRow.user_id)
                .where(SessionRow.id == session_id, SessionRow.deleted_at.is_(None))
            )
        ).one_or_none()
        if found is None:
            raise PublishError(404, "session not found")
        row, owner = found
        if not may_publish_for(owner):
            raise PublishError(409, "only the owner's and admins' notes are published")
        note = await latest_final_note(db, session_id)
        if note is None:
            raise PublishError(409, "this session has no note")
        path, title = note
        markdown = await asyncio.to_thread(read_note, outbox_dir(self._cfg), path)
        if markdown is None:
            raise PublishError(409, "the note is no longer in the outbox")
        return _Note(row, owner, path, row.title or title, markdown)

    @staticmethod
    async def _row(db: AsyncSession, session_id: uuid.UUID) -> NotePublication | None:
        return await db.get(NotePublication, session_id, populate_existing=True)

    # --- publish ----------------------------------------------------------------------------

    async def publish(self, session_id: uuid.UUID, actor: User) -> NotePublication:
        """Open (or reopen) the session's PR with the note as it is in the outbox."""
        gh = self._check_actor(actor)
        async with self.lock(session_id):
            async with self._sm() as db:
                note = await self._load(db, session_id)
                if note.session.status not in ("done", "failed"):
                    raise PublishError(409, "the session is not finished")
                pub = await self._row(db, session_id)
                if pub is not None and pub.state == "open":
                    raise PublishError(409, "a pull request is already open")
                if (
                    pub is not None
                    and pub.state == "merged"
                    and pub.content_sha256 == sha256(note.markdown)
                ):
                    raise PublishError(409, "already merged, and unchanged since")
                if pub is None:
                    pub = NotePublication(session_id=session_id, state="publishing")
                    db.add(pub)
                pub.state = "publishing"
                pub.repo_path = pub.repo_path or self._repo_path(note.path)
                pub.branch = self._branch(note.session)
                pub.published_by = actor.id
                pub.last_error = None
                pub.pr_number = pub.pr_url = pub.head_sha = None
                await db.commit()
                repo_path, branch = pub.repo_path, pub.branch
            try:
                base = await gh.branch_head(self._cfg.publish_base_branch)
                await gh.ensure_branch(branch, base)
                blob = await gh.file_sha(repo_path, branch)
                commit = await gh.put_file(
                    repo_path,
                    note.markdown,
                    branch=branch,
                    sha=blob,
                    message=self._commit_message(note.title, session_id),
                    committer=self._committer(),
                )
                pr = await gh.open_pr(
                    head=branch,
                    base=self._cfg.publish_base_branch,
                    title=f"jarvis-live: {note.title}",
                    body=self._pr_body(note, actor),
                )
            except GitHubError as e:
                log.warning("publishing %s failed: %s", session_id, e)
                await self._fail(session_id, str(e))
                raise PublishError(502, str(e)) from e
            async with self._sm() as db:
                pub = await self._row(db, session_id)
                assert pub is not None
                pub.state = "open"
                pub.pr_number, pub.pr_url = pr.number, pr.url
                pub.head_sha = commit
                pub.content_sha256 = sha256(note.markdown)
                pub.merged_at = None
                await db.commit()
                log.info("published %s as %s", session_id, pr.url)
                return pub

    async def _fail(self, session_id: uuid.UUID, error: str) -> None:
        async with self._sm() as db:
            pub = await self._row(db, session_id)
            if pub is not None:
                pub.state = "failed"
                pub.last_error = error[:2000]
                await db.commit()

    # --- the note ---------------------------------------------------------------------------

    async def edit_note(self, session_id: uuid.UUID, markdown: str) -> NotePublication | None:
        """Replace the session's note in the outbox; an open PR gets the edit pushed too.

        The caller has checked the user may see the session. The note must keep its
        ``session_id`` frontmatter line: the finalizer recognises its own file by it."""
        if len(markdown.encode("utf-8")) > MAX_NOTE_BYTES:
            raise PublishError(413, "the note is too large")
        if f"session_id: {session_id}" not in markdown:
            raise PublishError(422, f"keep the frontmatter line `session_id: {session_id}`")
        async with self.lock(session_id):
            async with self._sm() as db:
                row = await db.get(SessionRow, session_id)
                if row is None:
                    raise PublishError(404, "session not found")
                if row.status not in ("done", "failed"):
                    raise PublishError(409, "the note is being written")
                note = await latest_final_note(db, session_id)
                if note is None:
                    raise PublishError(409, "this session has no note")
                target = note_file(outbox_dir(self._cfg), note[0])
                if target is None:
                    raise PublishError(409, "the note is no longer in the outbox")
                await asyncio.to_thread(overwrite_atomic, target, markdown)
                pub = await self._row(db, session_id)
            if pub is None or pub.state != "open":
                return pub
            return await self._push(session_id, pub, markdown, note[1])

    async def _push(
        self, session_id: uuid.UUID, pub: NotePublication, markdown: str, title: str
    ) -> NotePublication:
        gh = self._github()
        assert pub.repo_path and pub.branch
        try:
            blob = await gh.file_sha(pub.repo_path, pub.branch)
            commit = await gh.put_file(
                pub.repo_path,
                markdown,
                branch=pub.branch,
                sha=blob,
                message=f"jarvis-live: edit {title}",
                committer=self._committer(),
            )
        except GitHubError as e:
            async with self._sm() as db:
                row = await self._row(db, session_id)
                if row is not None:
                    row.last_error = f"pushing the edit failed: {e}"[:2000]
                    await db.commit()
            raise PublishError(502, f"saved, but pushing the edit failed: {e}") from e
        async with self._sm() as db:
            row = await self._row(db, session_id)
            assert row is not None
            row.head_sha, row.content_sha256, row.last_error = commit, sha256(markdown), None
            await db.commit()
            return row

    # --- PR actions -------------------------------------------------------------------------

    async def merge(self, session_id: uuid.UUID, actor: User) -> NotePublication:
        gh = self._check_actor(actor)
        async with self.lock(session_id):
            pub, title = await self._open_pub(session_id)
            assert pub.pr_number is not None and pub.head_sha and pub.branch
            pr = await self._get_pr(gh, pub.pr_number)
            if pr.merged or pr.state != "open":
                return await self._settle(session_id, pr)
            if pr.head_sha != pub.head_sha:
                raise PublishError(
                    409, "the pull request changed on GitHub; review it there before merging"
                )
            for attempt in (1, 2):
                try:
                    await gh.merge(
                        pub.pr_number,
                        sha=pub.head_sha,
                        method=self._cfg.publish_merge_method,
                        title=f"jarvis-live: {title} (#{pub.pr_number})",
                    )
                    break
                except GitHubError as e:
                    # Right after the PR opens GitHub may not have computed mergeability yet.
                    if attempt == 1 and e.status == 405 and pr.mergeable is None:
                        await asyncio.sleep(2)
                        continue
                    raise PublishError(409 if e.status in (405, 409) else 502, str(e)) from e
            await self._drop_branch(gh, pub.branch)
            async with self._sm() as db:
                row = await self._row(db, session_id)
                assert row is not None
                row.state, row.merged_at, row.last_error = "merged", datetime.now(UTC), None
                await db.commit()
                return row

    async def close(self, session_id: uuid.UUID, actor: User) -> NotePublication:
        gh = self._check_actor(actor)
        async with self.lock(session_id):
            return await self._close_locked(gh, session_id)

    async def _close_locked(self, gh: GitHubClient, session_id: uuid.UUID) -> NotePublication:
        pub, _ = await self._open_pub(session_id)
        assert pub.pr_number is not None and pub.branch
        try:
            pr = await gh.close_pr(pub.pr_number)
        except GitHubError as e:
            raise PublishError(502, str(e)) from e
        await self._drop_branch(gh, pub.branch)
        return await self._settle(session_id, pr)

    async def skip(self, session_id: uuid.UUID, actor: User) -> NotePublication:
        """Keep this note out of publishing (and auto-publish). Closes an open PR."""
        if not may_publish_for(actor):
            raise PublishError(403, "only the owner and admins publish notes")
        async with self.lock(session_id):
            async with self._sm() as db:
                row = await db.get(SessionRow, session_id)
                owner = await db.get(User, row.user_id) if row else None
                if row is None or owner is None:
                    raise PublishError(404, "session not found")
                if not may_publish_for(owner):
                    raise PublishError(409, "only the owner's and admins' notes are published")
                pub = await self._row(db, session_id)
            if pub is not None and pub.state == "merged":
                raise PublishError(409, "already merged into the vault")
            if pub is not None and pub.state == "open":
                await self._close_locked(self._github(), session_id)
            async with self._sm() as db:
                pub = await self._row(db, session_id)
                if pub is None:
                    pub = NotePublication(session_id=session_id, state="skipped")
                    db.add(pub)
                pub.state, pub.last_error = "skipped", None
                await db.commit()
                return pub

    async def unskip(self, session_id: uuid.UUID, actor: User) -> NotePublication | None:
        if not may_publish_for(actor):
            raise PublishError(403, "only the owner and admins publish notes")
        async with self.lock(session_id):
            async with self._sm() as db:
                pub = await self._row(db, session_id)
                if pub is None or pub.state != "skipped":
                    raise PublishError(409, "the note is not skipped")
                if pub.pr_number is None:  # never published: back to "ready"
                    await db.delete(pub)
                    await db.commit()
                    return None
                pub.state = "closed"
                await db.commit()
                return pub

    async def _open_pub(self, session_id: uuid.UUID) -> tuple[NotePublication, str]:
        async with self._sm() as db:
            pub = await self._row(db, session_id)
            if pub is None or pub.state != "open":
                raise PublishError(409, "no open pull request")
            note = await latest_final_note(db, session_id)
            row = await db.get(SessionRow, session_id)
        title = (row.title if row else None) or (note[1] if note else "note")
        return pub, title

    async def _get_pr(self, gh: GitHubClient, number: int) -> PullRequest:
        try:
            return await gh.get_pr(number)
        except GitHubError as e:
            raise PublishError(502, str(e)) from e

    async def _drop_branch(self, gh: GitHubClient, branch: str) -> None:
        try:
            await gh.delete_branch(branch)
        except GitHubError as e:
            log.warning("could not delete branch %s: %s", branch, e)

    async def _settle(self, session_id: uuid.UUID, pr: PullRequest) -> NotePublication:
        """Record a PR that is no longer open: merged or closed (on GitHub or by us)."""
        async with self._sm() as db:
            row = await self._row(db, session_id)
            assert row is not None
            if pr.merged:
                row.state = "merged"
                row.merged_at = row.merged_at or datetime.now(UTC)
            elif pr.state != "open":
                row.state = "closed"
            await db.commit()
            return row

    # --- keeping up with GitHub -------------------------------------------------------------

    async def refresh(self, session_id: uuid.UUID, *, force: bool = False) -> None:
        """Pick up a merge or close done on GitHub. Rate-limited per session unless forced."""
        if self._gh is None:
            return
        now = time.monotonic()
        if not force and now - self._refreshed.get(session_id, 0.0) < REFRESH_MIN_INTERVAL_S:
            return
        self._refreshed[session_id] = now
        async with self.lock(session_id):
            async with self._sm() as db:
                pub = await self._row(db, session_id)
            if pub is None or pub.state != "open" or pub.pr_number is None:
                return
            try:
                pr = await self._gh.get_pr(pub.pr_number)
            except GitHubError as e:
                log.warning("refreshing %s failed: %s", session_id, e)
                return
            if pr.merged or pr.state != "open":
                await self._settle(session_id, pr)
                if pub.branch:
                    await self._drop_branch(self._gh, pub.branch)

    async def reconcile_once(self) -> None:
        if self._gh is None:
            return
        async with self._sm() as db:
            ids = (
                (
                    await db.execute(
                        select(NotePublication.session_id).where(NotePublication.state == "open")
                    )
                )
                .scalars()
                .all()
            )
        for sid in ids:
            await self.refresh(sid, force=True)

    async def run_reconcile_loop(self, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self.reconcile_once()
            except Exception:
                log.exception("publication reconcile failed")

    # --- auto-publish -----------------------------------------------------------------------

    async def on_note_ready(self, session_id: uuid.UUID) -> None:
        """Called after a note is written. Opens its PR if the session's user wants that and
        the note was never published or skipped (so a regeneration is not re-proposed)."""
        if self._gh is None:
            return
        async with self._sm() as db:
            found = (
                await db.execute(
                    select(User)
                    .join(SessionRow, SessionRow.user_id == User.id)
                    .where(SessionRow.id == session_id)
                )
            ).scalar_one_or_none()
            pub = await self._row(db, session_id)
        if found is None or not found.auto_publish or not may_publish_for(found) or pub:
            return
        try:
            await self.publish(session_id, found)
        except PublishError as e:
            log.warning("auto-publish of %s failed: %s", session_id, e.message)

    # --- the queue --------------------------------------------------------------------------

    async def queue(self, *, ready_limit: int = 200, recent_limit: int = 20) -> Queue:
        newest = (
            select(func.max(CopilotItem.id))
            .where(CopilotItem.kind == FINAL_NOTE_KIND)
            .group_by(CopilotItem.session_id)
        )
        latest = (
            select(CopilotItem.session_id, CopilotItem.payload)
            .where(CopilotItem.id.in_(newest))
            .subquery()
        )
        base = (
            select(SessionRow, User, latest.c.payload, NotePublication)
            .join(User, User.id == SessionRow.user_id)
            .join(latest, latest.c.session_id == SessionRow.id)
            .outerjoin(NotePublication, NotePublication.session_id == SessionRow.id)
            .where((User.id == OWNER_USER_ID) | User.is_admin, SessionRow.deleted_at.is_(None))
        )
        async with self._sm() as db:
            ready = (
                await db.execute(
                    base.where(
                        and_(SessionRow.status == "done", NotePublication.session_id.is_(None))
                    )
                    .order_by(SessionRow.started_at.desc())
                    .limit(ready_limit)
                )
            ).all()
            opened = (
                await db.execute(
                    base.where(NotePublication.state.in_(("open", "publishing"))).order_by(
                        NotePublication.updated_at.desc()
                    )
                )
            ).all()
            recent = (
                await db.execute(
                    base.where(NotePublication.state.in_(("merged", "closed", "failed", "skipped")))
                    .order_by(NotePublication.updated_at.desc())
                    .limit(recent_limit)
                )
            ).all()

        def item(r: Any) -> QueueItem:
            s, u, payload, p = r
            return QueueItem(
                session_id=s.id,
                title=s.title or str(payload["title"]),
                started_at=s.started_at,
                owner=u.display_name,
                note_path=str(payload["path"]),
                publication=p,
            )

        return Queue(
            ready=[item(r) for r in ready],
            open=[item(r) for r in opened],
            recent=[item(r) for r in recent],
        )

    def start_publish_all(self, actor: User) -> Literal["started", "running"]:
        """Publish every ready note, one at a time, in the background."""
        self._check_actor(actor)
        if self.publishing_all:
            return "running"
        self._publish_all = asyncio.create_task(self._run_publish_all(actor))
        return "started"

    async def _run_publish_all(self, actor: User) -> None:
        q = await self.queue(ready_limit=1000)
        for i, it in enumerate(q.ready):
            if i:
                await asyncio.sleep(PUBLISH_ALL_GAP_S)
            try:
                await self.publish(it.session_id, actor)
            except PublishError as e:
                log.warning("publish-all: %s failed: %s", it.session_id, e.message)

    async def shutdown(self) -> None:
        if self._publish_all is not None:
            self._publish_all.cancel()
            await asyncio.gather(self._publish_all, return_exceptions=True)

    # --- naming -----------------------------------------------------------------------------

    def _repo_path(self, outbox_path: str) -> str:
        sub = self._cfg.publish_subdir
        return f"{sub}/{outbox_path}" if sub else outbox_path

    def _branch(self, row: SessionRow) -> str:
        day = row.started_at.astimezone(ZoneInfo(self._cfg.timezone)).date().isoformat()
        return f"{self._cfg.publish_branch_prefix}{day}-{row.id.hex[:8]}"

    def _committer(self) -> dict[str, str]:
        return {
            "name": self._cfg.publish_committer_name,
            "email": self._cfg.publish_committer_email,
        }

    @staticmethod
    def _commit_message(title: str, session_id: uuid.UUID) -> str:
        return f"jarvis-live: {title}\n\nSession {session_id}."

    def _pr_body(self, note: _Note, actor: User) -> str:
        tz = ZoneInfo(self._cfg.timezone)
        started = note.session.started_at.astimezone(tz)
        lines = [
            f"Jarvis Live note for session `{note.session.id}`.",
            "",
            f"- Recorded: {started:%Y-%m-%d %H:%M} ({self._cfg.timezone})",
            f"- Recorded by: {note.owner.display_name}",
            f"- File: `{self._repo_path(note.path)}`",
        ]
        if base := self._cfg.public_base_url.rstrip("/"):
            lines.append(f"- Session: {base}/sessions/{note.session.id}")
        lines += [
            "",
            f"Proposed by {actor.display_name} from Jarvis Live. Merge here or from the session "
            "page; edits made in Jarvis Live while this is open are pushed to this branch.",
        ]
        return "\n".join(lines)
