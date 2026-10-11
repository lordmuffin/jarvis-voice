"""Managing a session's note: edit it, regenerate it, and publish it to the vault repo.

Publishing mirrors capture's vault-writer: one branch and one PR per session against the
vault's ``main``, then merge (or close) from here or on GitHub. See ``publish/service.py``."""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import update

from jarvis_live.api.sessions import (
    Db,
    PrincipalDep,
    PublicationOut,
    SessionDetail,
    _visible,
    get_session,
)
from jarvis_live.db.models import NotePublication
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.finalize.outbox import latest_final_note
from jarvis_live.logging_setup import session_logger
from jarvis_live.orchestrator import Orchestrator
from jarvis_live.publish.service import (
    MAX_NOTE_BYTES,
    PublishError,
    PublishService,
    QueueItem,
)

router = APIRouter(prefix="/v1")


def _service(request: Request) -> PublishService:
    service: PublishService = request.app.state.publishing
    return service


def _raise(e: PublishError) -> HTTPException:
    return HTTPException(e.status, e.message)


class NoteEdit(BaseModel):
    markdown: str = Field(min_length=1, max_length=MAX_NOTE_BYTES)


@router.put("/sessions/{session_id}/note")
async def edit_note(
    session_id: uuid.UUID, body: NoteEdit, principal: PrincipalDep, db: Db, request: Request
) -> SessionDetail:
    """Replace the note's Markdown. If its PR is open, the edit is pushed to the PR too."""
    await _visible(db, principal, session_id)
    try:
        await _service(request).edit_note(session_id, body.markdown)
    except PublishError as e:
        raise _raise(e) from e
    session_logger(__name__, session_id).info("note edited")
    return await get_session(session_id, principal, db, request)


@router.post("/sessions/{session_id}/regenerate")
async def regenerate(
    session_id: uuid.UUID, principal: PrincipalDep, db: Db, request: Request
) -> SessionDetail:
    """Write the note again from the transcript. The session is ``finalizing`` meanwhile."""
    row, _ = await _visible(db, principal, session_id)
    orchestrator: Orchestrator | None = request.app.state.orchestrator
    if orchestrator is None:
        raise HTTPException(503, "no LLM is configured")
    if row.local_only:
        raise HTTPException(409, "local-only sessions are never sent to an LLM")
    service = _service(request)
    async with service.lock(session_id):
        pub = await db.get(NotePublication, session_id, populate_existing=True)
        if pub is not None and pub.state in ("open", "publishing"):
            raise HTTPException(409, "close the pull request first")
        # A session stuck in ``finalizing`` with no run in flight (e.g. a restart beyond the
        # recovery window) may be regenerated too.
        allowed = ["done", "failed"]
        if not orchestrator.is_finalizing(session_id):
            allowed.append("finalizing")
        res = await db.execute(
            update(SessionRow)
            .where(SessionRow.id == session_id, SessionRow.status.in_(allowed))
            .values(status="finalizing")
        )
        if res.rowcount == 0:  # type: ignore[attr-defined]
            raise HTTPException(409, "the session is not finished")
        await db.commit()
        note = await latest_final_note(db, session_id)
        orchestrator.regenerate(session_id, note[0] if note else None)
    session_logger(__name__, session_id).info("note regeneration started")
    return await get_session(session_id, principal, db, request)


class PublicationState(BaseModel):
    publication: PublicationOut | None


@router.get("/sessions/{session_id}/publication")
async def get_publication(
    session_id: uuid.UUID, principal: PrincipalDep, db: Db, request: Request
) -> PublicationState:
    """The publication, after checking an open PR on GitHub (it may have been merged there)."""
    await _visible(db, principal, session_id)
    await _service(request).refresh(session_id)
    pub = await db.get(NotePublication, session_id, populate_existing=True)
    return PublicationState(publication=PublicationOut.of(pub) if pub else None)


Action = Literal["publish", "merge", "close", "skip", "unskip"]


async def _act(
    action: Action, session_id: uuid.UUID, principal: PrincipalDep, db: Db, request: Request
) -> SessionDetail:
    await _visible(db, principal, session_id)
    service, user = _service(request), principal.user
    try:
        if action == "publish":
            await service.publish(session_id, user)
        elif action == "merge":
            await service.merge(session_id, user)
        elif action == "close":
            await service.close(session_id, user)
        elif action == "skip":
            await service.skip(session_id, user)
        else:
            await service.unskip(session_id, user)
    except PublishError as e:
        raise _raise(e) from e
    session_logger(__name__, session_id).info("publication %s", action)
    return await get_session(session_id, principal, db, request)


@router.post("/sessions/{session_id}/publication")
async def publish(
    session_id: uuid.UUID, principal: PrincipalDep, db: Db, request: Request
) -> SessionDetail:
    """Open a PR with the note against the vault repo's base branch."""
    return await _act("publish", session_id, principal, db, request)


@router.post("/sessions/{session_id}/publication/merge")
async def merge(
    session_id: uuid.UUID, principal: PrincipalDep, db: Db, request: Request
) -> SessionDetail:
    return await _act("merge", session_id, principal, db, request)


@router.post("/sessions/{session_id}/publication/close")
async def close(
    session_id: uuid.UUID, principal: PrincipalDep, db: Db, request: Request
) -> SessionDetail:
    return await _act("close", session_id, principal, db, request)


@router.post("/sessions/{session_id}/publication/skip")
async def skip(
    session_id: uuid.UUID, principal: PrincipalDep, db: Db, request: Request
) -> SessionDetail:
    """Never publish this note (closes an open PR)."""
    return await _act("skip", session_id, principal, db, request)


@router.delete("/sessions/{session_id}/publication/skip")
async def unskip(
    session_id: uuid.UUID, principal: PrincipalDep, db: Db, request: Request
) -> SessionDetail:
    return await _act("unskip", session_id, principal, db, request)


# --- the publishing queue (owner and admins) ---------------------------------------------------


class QueueItemOut(BaseModel):
    session_id: uuid.UUID
    title: str
    started_at: datetime
    owner: str
    note_path: str
    publication: PublicationOut | None

    @classmethod
    def of(cls, it: QueueItem) -> "QueueItemOut":
        return cls(
            session_id=it.session_id,
            title=it.title,
            started_at=it.started_at,
            owner=it.owner,
            note_path=it.note_path,
            publication=PublicationOut.of(it.publication) if it.publication else None,
        )


class QueueOut(BaseModel):
    repo: str
    base_branch: str
    auto_publish: bool  # the caller's own setting
    publishing_all: bool  # a "publish all" is running
    ready: list[QueueItemOut]  # finished notes never published or skipped
    open: list[QueueItemOut]
    recent: list[QueueItemOut]  # merged, closed, failed, skipped


def _publisher(principal: PrincipalDep, request: Request) -> PublishService:
    service = _service(request)
    if not service.enabled:
        raise HTTPException(503, "publishing is not configured")
    if not service.can_publish(principal.user):
        raise HTTPException(403, "only the owner and admins publish notes")
    return service


async def _queue(service: PublishService, principal: PrincipalDep, request: Request) -> QueueOut:
    q = await service.queue()
    return QueueOut(
        repo=service.repo,
        base_branch=request.app.state.settings.publish_base_branch,
        auto_publish=principal.user.auto_publish,
        publishing_all=service.publishing_all,
        ready=[QueueItemOut.of(i) for i in q.ready],
        open=[QueueItemOut.of(i) for i in q.open],
        recent=[QueueItemOut.of(i) for i in q.recent],
    )


@router.get("/publishing")
async def publishing_queue(principal: PrincipalDep, request: Request) -> QueueOut:
    return await _queue(_publisher(principal, request), principal, request)


@router.post("/publishing/sync")
async def sync(principal: PrincipalDep, request: Request) -> QueueOut:
    """Check every open PR on GitHub now (the loop does this every few minutes)."""
    service = _publisher(principal, request)
    await service.reconcile_once()
    return await _queue(service, principal, request)


class PublishAllOut(BaseModel):
    status: Literal["started", "running"]


@router.post("/publishing/publish-all", status_code=202)
async def publish_all(principal: PrincipalDep, request: Request) -> PublishAllOut:
    """Publish every ready note, one PR each, in the background."""
    service = _publisher(principal, request)
    try:
        return PublishAllOut(status=service.start_publish_all(principal.user))
    except PublishError as e:
        raise _raise(e) from e
