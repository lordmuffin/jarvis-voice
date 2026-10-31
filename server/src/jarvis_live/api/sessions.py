import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_live.api.audio import audio_available, wav_response
from jarvis_live.auth import Principal, current_principal
from jarvis_live.db.models import (
    CopilotItem,
    MarkerRow,
    NotePublication,
    SegmentRow,
    SessionSpeaker,
    User,
)
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.finalize.note_items import merge_note_items
from jarvis_live.finalize.outbox import latest_final_note, outbox_dir, read_note
from jarvis_live.finalize.restriction import lift_restriction
from jarvis_live.ingest.upload import UndecodableAudio, decode_to_pcm
from jarvis_live.logging_setup import session_logger
from jarvis_live.processing import ProcessingState, derive
from jarvis_live.protocol import (
    Copilot,
    SessionCreateRequest,
    SessionCreateResponse,
    TicketRequest,
    TicketResponse,
)
from jarvis_live.publish.service import PublishService, may_publish_for, sha256

router = APIRouter(prefix="/v1/sessions")

PrincipalDep = Annotated[Principal, Depends(current_principal)]


async def _db(request: Request) -> Any:
    async with request.app.state.sessionmaker() as db:
        yield db


Db = Annotated[AsyncSession, Depends(_db)]


class OwnerOut(BaseModel):
    id: uuid.UUID
    display_name: str


class SessionOut(BaseModel):
    id: uuid.UUID
    title: str | None
    mode: str
    status: str
    channels: list[str]
    started_at: datetime
    ended_at: datetime | None
    local_only: bool
    # A work call: its note is filed apart and nothing leaves the homelab (see ``publish/``).
    restricted: bool
    # "watch" for a recording made on the Wear OS app; None when the uploader did not say.
    source: str | None
    # Audio is arriving right now. ``status`` stays ``live`` while a dropped producer may still
    # reconnect, so viewers use this to tell "live" from "live but nothing streaming".
    streaming: bool
    owner: OwnerOut
    # Set while the session is in "Recently deleted"; ``purge_after`` is the owner's own
    # "delete after N days" deadline. Both are null for an ordinary session.
    deleted_at: datetime | None = None
    purge_after: datetime | None = None
    # When the trash deletes it for good (``deleted_at`` plus the trash window).
    trash_expires_at: datetime | None = None

    @classmethod
    def of(cls, s: SessionRow, owner: User, request: Request) -> "SessionOut":
        return cls(
            id=s.id,
            title=s.title,
            mode=s.mode,
            status=s.status,
            channels=list(s.channels),
            started_at=s.started_at,
            ended_at=s.ended_at,
            local_only=s.local_only,
            restricted=s.restricted,
            source=s.source,
            streaming=s.status == "live" and request.app.state.hub.is_streaming(s.id),
            owner=OwnerOut(id=owner.id, display_name=owner.display_name),
            deleted_at=s.deleted_at,
            purge_after=s.purge_after,
            trash_expires_at=None
            if s.deleted_at is None
            else s.deleted_at + timedelta(days=request.app.state.settings.trash_retention_days),
        )


class SessionUpdate(BaseModel):
    """Only the fields sent are changed."""

    # Blank or null clears the title; the copilot may then name the session again.
    title: str | None = Field(default=None, max_length=200)
    # Delete the session (to "Recently deleted") this many days from now; null cancels.
    delete_after_days: int | None = Field(default=None, ge=1, le=3650)

    @model_validator(mode="after")
    def _says_something(self) -> "SessionUpdate":
        if not self.model_fields_set & {"title", "delete_after_days"}:
            raise ValueError("send a title or delete_after_days")
        return self

    @field_validator("title")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return (v or "").strip() or None


class SessionPage(BaseModel):
    items: list[SessionOut]
    total: int
    limit: int
    offset: int


class SegmentOut(BaseModel):
    id: uuid.UUID
    channel: str
    speaker: str
    start_ms: int
    end_ms: int
    text: str
    stt_tier: str | None
    # Who spoke it, once diarization ran: an id into the session's ``speakers``. ``speaker``
    # above stays the channel side.
    speaker_id: str | None = None
    speaker_rev: int = 0
    mixed: bool = False


class SpeakerOut(BaseModel):
    id: str
    label: str | None  # None: not named, shown as "Speaker <ordinal>"
    ordinal: int
    enrolled: bool
    is_owner: bool


class MarkerOut(BaseModel):
    t_ms: int
    label: str


class FinalNoteOut(BaseModel):
    path: str
    title: str
    markdown: str | None  # None if the file is no longer in the outbox


class PublicationOut(BaseModel):
    """Where a session's note stands on its way to the vault repo (see ``publish/``)."""

    state: Literal["publishing", "open", "merged", "closed", "skipped", "failed"]
    repo_path: str | None
    branch: str | None
    pr_number: int | None
    pr_url: str | None
    last_error: str | None
    updated_at: datetime
    merged_at: datetime | None
    # The note in the outbox differs from what was last pushed. None: not known here.
    changed: bool | None = None

    @classmethod
    def of(cls, p: NotePublication, markdown: str | None = None) -> "PublicationOut":
        changed = None
        if markdown is not None and p.content_sha256 is not None:
            changed = sha256(markdown) != p.content_sha256
        return cls(
            state=p.state,
            repo_path=p.repo_path,
            branch=p.branch,
            pr_number=p.pr_number,
            pr_url=p.pr_url,
            last_error=p.last_error,
            updated_at=p.updated_at,
            merged_at=p.merged_at,
            changed=changed,
        )


class SessionDetail(SessionOut):
    segments: list[SegmentOut]
    speakers: list[SpeakerOut]
    copilot: Copilot
    markers: list[MarkerOut]
    final_note: FinalNoteOut | None
    # Which channels' recordings can be played (``GET .../audio/{channel}.wav``).
    audio: dict[str, bool]
    publication: PublicationOut | None
    # What the caller may do with the note: publish it to the vault repo, re-run the LLM.
    can_publish: bool
    can_regenerate: bool
    # What transcription and the note are doing now (``POST .../process`` re-runs both).
    processing: ProcessingState


def processing_of(request: Request, row: SessionRow) -> ProcessingState:
    """From the jobs in flight in this process: the hub (streaming, draining an ended session),
    uploads being ingested, and the orchestrator's finalize/regenerate/reprocess runs."""
    state = request.app.state
    orchestrator = state.orchestrator
    # Whichever transcription of known length is in flight: at most one at a time.
    measures = (
        orchestrator.progress(row.id) if orchestrator else None,
        state.hub.draining_progress(row.id),
        state.uploads.progress(row.id),
    )
    return derive(
        row.status,
        started_at=row.started_at,
        local_only=row.local_only,
        streaming=row.status == "live" and state.hub.is_streaming(row.id),
        uploading_since=state.uploads.since(row.id),
        draining_since=state.hub.draining_since(row.id),
        job=orchestrator.job(row.id) if orchestrator else None,
        transcription_failed=bool(orchestrator and orchestrator.transcription_failed(row.id)),
        progress=next((p for p in measures if p is not None), None),
    )


# Every session belongs to a user. Its owner (from any of their devices or the web dashboard)
# and admins can read and rename it; only its owner can stream into it or end it. Anyone else
# gets 404, as if it did not exist.


async def _visible(
    db: AsyncSession,
    principal: Principal,
    session_id: uuid.UUID,
    *,
    include_deleted: bool = False,
) -> tuple[SessionRow, User]:
    """A soft-deleted session is as good as gone, unless the caller asks for the trash."""
    query = (
        select(SessionRow, User)
        .join(User, User.id == SessionRow.user_id)
        .where(SessionRow.id == session_id)
    )
    if not include_deleted:
        query = query.where(SessionRow.deleted_at.is_(None))
    found = (await db.execute(query)).one_or_none()
    if found is None or (found[0].user_id != principal.user.id and not principal.is_admin):
        raise HTTPException(404, "Session not found.")
    return found[0], found[1]


async def _owned(db: AsyncSession, principal: Principal, session_id: uuid.UUID) -> SessionRow:
    row, _ = await _visible(db, principal, session_id)
    if row.user_id != principal.user.id:
        raise HTTPException(404, "Session not found.")
    return row


async def _writable(
    db: AsyncSession,
    principal: Principal,
    session_id: uuid.UUID,
    *,
    include_deleted: bool = False,
) -> tuple[SessionRow, User]:
    """Read access is not write access: an admin can see another user's session (and rename it),
    but its note, its jobs and its publication are its owner's. 404 if not visible at all, 403
    if visible but not theirs."""
    row, owner = await _visible(db, principal, session_id, include_deleted=include_deleted)
    if row.user_id != principal.user.id:
        raise HTTPException(403, "Only the session's owner can do this.")
    return row, owner


@router.post("", status_code=201)
async def create_session(
    body: SessionCreateRequest, principal: PrincipalDep, db: Db
) -> SessionCreateResponse:
    device = principal.device
    if device is None:
        raise HTTPException(403, "Sessions are recorded from a device.")
    row = SessionRow(
        device_id=device.id,
        user_id=principal.user.id,
        title=body.title,
        mode=body.mode,
        status="live",
        channels=list(body.channels),
        restricted=bool(body.restricted),
    )
    db.add(row)
    await db.commit()
    session_logger(__name__, row.id).info("session created", extra={"device": device.name})
    return SessionCreateResponse(id=row.id)


def _upload_title(title: str | None, filename: str | None) -> str | None:
    """The given title, else the file name without its extension; blank is None."""
    return (title or "").strip() or PurePath(filename or "").stem.strip() or None


@router.post("/upload", status_code=202)
async def upload_recording(
    principal: PrincipalDep,
    db: Db,
    request: Request,
    title: Annotated[str | None, Query(max_length=200)] = None,
    filename: Annotated[str | None, Query(max_length=255)] = None,
    mode: Literal["meeting", "solo"] = "solo",
    restricted: bool = False,
    source: Literal["watch", "phone", "web"] | None = None,
    client_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> SessionCreateResponse:
    """A recorded file as the raw request body (any format ffmpeg reads). It becomes a mic-only
    session, transcribed in the background like a live one; the title defaults to the file
    name. 415 if it holds no audio, 413 over ``upload_max_mb``.

    ``source`` tags where it was recorded. ``client_id`` is the uploader's id for the recording:
    repeating an upload with the same one returns the existing session (202 again) instead of a
    second, so a client that never saw the response can retry until it does. The 202 is the
    server's acknowledgement that the audio is stored: only then may the uploader delete it."""
    if client_id is not None:
        existing = (
            await db.execute(
                select(SessionRow.id).where(
                    SessionRow.user_id == principal.user.id, SessionRow.client_id == client_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return SessionCreateResponse(id=existing)
    cfg = request.app.state.settings
    limit = cfg.upload_max_mb * 1024 * 1024
    if int(request.headers.get("content-length") or 0) > limit:
        raise HTTPException(413, f"Recordings are limited to {cfg.upload_max_mb} MB.")
    uploads = cfg.data_dir / "uploads"
    await asyncio.to_thread(uploads.mkdir, parents=True, exist_ok=True)
    key = uuid.uuid4().hex
    src, pcm = uploads / f"{key}.upload", uploads / f"{key}.pcm"
    try:
        size = 0
        with src.open("wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"Recordings are limited to {cfg.upload_max_mb} MB.")
                f.write(chunk)
        if size == 0:
            raise HTTPException(422, "The upload is empty.")
        try:
            await decode_to_pcm(src, pcm, ffmpeg=cfg.ffmpeg_path)
        except UndecodableAudio as e:
            raise HTTPException(415, "No audio could be read from the file.") from e
    except BaseException:
        pcm.unlink(missing_ok=True)
        raise
    finally:
        src.unlink(missing_ok=True)
    row = SessionRow(
        device_id=principal.device.id if principal.device else None,
        user_id=principal.user.id,
        title=_upload_title(title, filename),
        mode=mode,
        status="live",
        channels=["mic"],
        restricted=restricted,
        source=source,
        client_id=client_id,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:  # two retries raced past the check above
        await db.rollback()
        pcm.unlink(missing_ok=True)
        raced = (
            await db.execute(
                select(SessionRow.id).where(
                    SessionRow.user_id == principal.user.id, SessionRow.client_id == client_id
                )
            )
        ).scalar_one()
        return SessionCreateResponse(id=raced)
    session_logger(__name__, row.id).info("recording uploaded", extra={"bytes": size})
    request.app.state.uploads.start(row.id, pcm)
    return SessionCreateResponse(id=row.id)


@router.get("")
async def list_sessions(
    principal: PrincipalDep,
    db: Db,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    all_users: Annotated[bool, Query(alias="all")] = False,
    user_id: uuid.UUID | None = None,
    deleted: bool = False,
) -> SessionPage:
    """The caller's sessions, newest first. Admins may pass ``all=true`` for everyone's, or
    ``user_id`` for one user's. ``deleted=true`` lists the trash instead ("Recently deleted"),
    the most recently deleted first."""
    if (all_users or user_id is not None) and not principal.is_admin:
        raise HTTPException(403, "Admin only.")
    where: list[ColumnElement[bool]] = [
        SessionRow.deleted_at.is_not(None) if deleted else SessionRow.deleted_at.is_(None)
    ]
    if user_id is not None:
        where.append(SessionRow.user_id == user_id)
    elif not all_users:
        where.append(SessionRow.user_id == principal.user.id)
    total = (
        await db.execute(select(func.count()).select_from(SessionRow).where(*where))
    ).scalar_one()
    rows = (
        await db.execute(
            select(SessionRow, User)
            .join(User, User.id == SessionRow.user_id)
            .where(*where)
            .order_by(
                SessionRow.deleted_at.desc() if deleted else SessionRow.started_at.desc(),
                SessionRow.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return SessionPage(
        items=[SessionOut.of(r, u, request) for r, u in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{session_id}")
async def get_session(
    session_id: uuid.UUID,
    principal: PrincipalDep,
    db: Db,
    request: Request,
    include_deleted: bool = False,
) -> SessionDetail:
    row, owner = await _visible(db, principal, session_id, include_deleted=include_deleted)
    segments = (
        (
            await db.execute(
                select(SegmentRow)
                .where(SegmentRow.session_id == session_id, SegmentRow.source == "final")
                .order_by(SegmentRow.start_ms, SegmentRow.created_at)
            )
        )
        .scalars()
        .all()
    )
    speakers = (
        (
            await db.execute(
                select(SessionSpeaker)
                .where(SessionSpeaker.session_id == session_id)
                .order_by(SessionSpeaker.ordinal)
            )
        )
        .scalars()
        .all()
    )
    latest = (
        await db.execute(
            select(CopilotItem.payload)
            .where(CopilotItem.session_id == session_id, CopilotItem.kind == "snapshot")
            .order_by(CopilotItem.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    copilot = (
        Copilot.model_validate(latest)
        if latest is not None
        else Copilot(version=0, notes=[], actions=[], decisions=[], suggestions=[], related=[])
    )
    markers = (
        (
            await db.execute(
                select(MarkerRow)
                .where(MarkerRow.session_id == session_id)
                .order_by(MarkerRow.t_ms, MarkerRow.id)
            )
        )
        .scalars()
        .all()
    )
    final_note: FinalNoteOut | None = None
    if (found := await latest_final_note(db, session_id)) is not None:
        path, title = found
        markdown = await asyncio.to_thread(read_note, outbox_dir(request.app.state.settings), path)
        final_note = FinalNoteOut(path=path, title=title, markdown=markdown)
        # The live copilot may never have run (an upload, a short or solo session): the actions
        # and decisions the note lists show alongside its own, without repeats.
        copilot = merge_note_items(copilot, markdown)
    pub = await db.get(NotePublication, session_id, populate_existing=True)
    service: PublishService = request.app.state.publishing
    data_dir = request.app.state.settings.data_dir
    audio = {c: await asyncio.to_thread(audio_available, data_dir, row.id, c) for c in row.channels}
    return SessionDetail(
        **SessionOut.of(row, owner, request).model_dump(),
        audio=audio,
        publication=PublicationOut.of(pub, final_note.markdown if final_note else None)
        if pub
        else None,
        can_publish=service.can_publish(principal.user)
        and may_publish_for(owner)
        and not row.restricted,
        can_regenerate=request.app.state.orchestrator is not None and not row.local_only,
        processing=processing_of(request, row),
        markers=[MarkerOut(t_ms=m.t_ms, label=m.label) for m in markers],
        final_note=final_note,
        segments=[
            SegmentOut(
                id=s.id,
                channel=s.channel,
                speaker=s.speaker,
                start_ms=s.start_ms,
                end_ms=s.end_ms,
                text=s.text,
                stt_tier=s.stt_tier,
                speaker_id=s.speaker_id,
                speaker_rev=s.speaker_rev,
                mixed=s.mixed,
            )
            for s in segments
        ],
        speakers=[
            SpeakerOut(
                id=sp.speaker_id,
                label=sp.label,
                ordinal=sp.ordinal,
                enrolled=False,
                is_owner=sp.is_owner,
            )
            for sp in speakers
        ],
        copilot=copilot,
    )


@router.get("/{session_id}/processing")
async def get_processing(
    session_id: uuid.UUID, principal: PrincipalDep, db: Db, request: Request
) -> ProcessingState:
    """Just the processing state, cheap enough to poll while a job runs."""
    row, _ = await _visible(db, principal, session_id)
    return processing_of(request, row)


@router.post("/{session_id}/ticket")
async def issue_ticket(
    session_id: uuid.UUID, body: TicketRequest, principal: PrincipalDep, db: Db, request: Request
) -> TicketResponse:
    if body.role == "producer":
        if (await _owned(db, principal, session_id)).status != "live":
            raise HTTPException(409, "This session is not live.")
    else:
        await _visible(db, principal, session_id)
    ticket = request.app.state.tickets.issue(session_id, principal.user.id, body.role)
    return TicketResponse(ticket=ticket, expires_in=60)


@router.post("/{session_id}/end")
async def end_session(
    session_id: uuid.UUID, principal: PrincipalDep, db: Db, request: Request
) -> SessionOut:
    await _owned(db, principal, session_id)
    await request.app.state.hub.end(session_id)
    row = await db.get(SessionRow, session_id, populate_existing=True)
    assert row is not None
    return SessionOut.of(row, principal.user, request)


@router.patch("/{session_id}")
async def update_session(
    session_id: uuid.UUID, body: SessionUpdate, principal: PrincipalDep, db: Db, request: Request
) -> SessionOut:
    if "delete_after_days" in body.model_fields_set:  # the deadline is the owner's
        row, owner = await _writable(db, principal, session_id)
        days = body.delete_after_days
        row.purge_after = None if days is None else datetime.now(UTC) + timedelta(days=days)
    else:
        row, owner = await _visible(db, principal, session_id)
    if "title" in body.model_fields_set:
        row.title = body.title
    await db.commit()
    session_logger(__name__, row.id).info("session updated")
    return SessionOut.of(row, owner, request)


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: uuid.UUID, principal: PrincipalDep, db: Db) -> None:
    """Move the session to "Recently deleted". Its audio and transcript are kept until the
    trash window ends; ``POST .../restore`` brings it back. Repeating it changes nothing."""
    row, _ = await _writable(db, principal, session_id, include_deleted=True)
    if row.deleted_at is not None:
        return
    if row.status in ("live", "finalizing"):
        raise HTTPException(409, "End the session before deleting it.")
    row.deleted_at = datetime.now(UTC)
    await db.commit()
    session_logger(__name__, row.id).info("session deleted")


@router.post("/{session_id}/restore")
async def restore_session(
    session_id: uuid.UUID, principal: PrincipalDep, db: Db, request: Request
) -> SessionOut:
    row, owner = await _writable(db, principal, session_id, include_deleted=True)
    if row.deleted_at is None:
        raise HTTPException(404, "Session not found.")
    row.deleted_at = None
    row.purge_after = None  # a pending delete-after would only delete it again
    await db.commit()
    session_logger(__name__, row.id).info("session restored")
    return SessionOut.of(row, owner, request)


@router.post("/{session_id}/unrestrict")
async def unrestrict_session(
    session_id: uuid.UUID, principal: PrincipalDep, db: Db, request: Request
) -> SessionOut:
    """Lift a session's restriction: its note moves out of ``Restricted/`` and may be published
    like any other. Only the session's owner may, and only once the session has finished: its
    note is not being written. Runs under the note's lock, which regenerating and reprocessing
    take to reopen a session, so the status read here holds until the note has moved. A session
    that is not restricted is returned as it is."""
    row = await _owned(db, principal, session_id)
    if row.restricted:
        async with request.app.state.publishing.lock(session_id):
            await db.refresh(row)
            if row.status not in ("done", "failed"):
                raise HTTPException(409, "The note is being written. Try again when it is done.")
            await lift_restriction(
                request.app.state.settings, request.app.state.sessionmaker, row.id
            )
        await db.refresh(row)
        session_logger(__name__, row.id).info("restriction lifted")
    return SessionOut.of(row, principal.user, request)


@router.get("/{session_id}/audio/{channel}.wav")
async def get_audio(
    session_id: uuid.UUID,
    channel: Literal["mic", "system"],
    principal: PrincipalDep,
    db: Db,
    request: Request,
) -> Response:
    """The session's recording of one channel as a WAV file. Supports ``Range``."""
    row, _ = await _visible(db, principal, session_id)
    if channel not in row.channels:
        raise HTTPException(404, "No such channel.")
    return await wav_response(request, request.app.state.settings.data_dir, row.id, channel)
