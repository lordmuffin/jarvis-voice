import asyncio
import uuid
from datetime import datetime
from pathlib import PurePath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_live.api import session_list as sl
from jarvis_live.api.audio import audio_available, wav_response
from jarvis_live.auth import Principal, current_principal
from jarvis_live.db.models import CopilotItem, MarkerRow, NotePublication, SegmentRow, User
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.finalize.outbox import latest_final_note, outbox_dir, read_note
from jarvis_live.ingest.upload import UndecodableAudio, decode_to_pcm
from jarvis_live.logging_setup import session_logger
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


class MatchOut(BaseModel):
    """Where a transcript search hit: the line's start, and its text with the match marked
    between U+E000 and U+E001."""

    start_ms: int
    snippet: str


class SessionOut(BaseModel):
    id: uuid.UUID
    title: str | None
    mode: str
    status: str
    channels: list[str]
    started_at: datetime
    ended_at: datetime | None
    local_only: bool
    # Audio is arriving right now. ``status`` stays ``live`` while a dropped producer may still
    # reconnect, so viewers use this to tell "live" from "live but nothing streaming".
    streaming: bool
    owner: OwnerOut
    # Only on a transcript search.
    match: MatchOut | None = None

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
            streaming=s.status == "live" and request.app.state.hub.is_streaming(s.id),
            owner=OwnerOut(id=owner.id, display_name=owner.display_name),
        )


class SessionUpdate(BaseModel):
    # Blank or null clears the title; the copilot may then name the session again.
    title: str | None = Field(max_length=200)

    @field_validator("title")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return (v or "").strip() or None


class SessionPage(BaseModel):
    items: list[SessionOut]
    total: int
    limit: int
    offset: int
    # Sessions per status under every filter *except* status, for the filter's own counts.
    counts: dict[str, int]


class SegmentOut(BaseModel):
    id: uuid.UUID
    channel: str
    speaker: str
    start_ms: int
    end_ms: int
    text: str
    stt_tier: str | None


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
    copilot: Copilot
    markers: list[MarkerOut]
    final_note: FinalNoteOut | None
    # Which channels' recordings can be played (``GET .../audio/{channel}.wav``).
    audio: dict[str, bool]
    publication: PublicationOut | None
    # What the caller may do with the note: publish it to the vault repo, re-run the LLM.
    can_publish: bool
    can_regenerate: bool


# Every session belongs to a user. Its owner (from any of their devices or the web dashboard)
# and admins can read and rename it; only its owner can stream into it or end it. Anyone else
# gets 404, as if it did not exist.


async def _visible(
    db: AsyncSession, principal: Principal, session_id: uuid.UUID
) -> tuple[SessionRow, User]:
    found = (
        await db.execute(
            select(SessionRow, User)
            .join(User, User.id == SessionRow.user_id)
            .where(SessionRow.id == session_id)
        )
    ).one_or_none()
    if found is None or (found[0].user_id != principal.user.id and not principal.is_admin):
        raise HTTPException(404, "session not found")
    return found[0], found[1]


async def _owned(db: AsyncSession, principal: Principal, session_id: uuid.UUID) -> SessionRow:
    row, _ = await _visible(db, principal, session_id)
    if row.user_id != principal.user.id:
        raise HTTPException(404, "session not found")
    return row


@router.post("", status_code=201)
async def create_session(
    body: SessionCreateRequest, principal: PrincipalDep, db: Db
) -> SessionCreateResponse:
    device = principal.device
    if device is None:
        raise HTTPException(403, "sessions are recorded from a device")
    row = SessionRow(
        device_id=device.id,
        user_id=principal.user.id,
        title=body.title,
        mode=body.mode,
        status="live",
        channels=list(body.channels),
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
) -> SessionCreateResponse:
    """A recorded file as the raw request body (any format ffmpeg reads). It becomes a mic-only
    session, transcribed in the background like a live one; the title defaults to the file
    name. 415 if it holds no audio, 413 over ``upload_max_mb``."""
    cfg = request.app.state.settings
    limit = cfg.upload_max_mb * 1024 * 1024
    if int(request.headers.get("content-length") or 0) > limit:
        raise HTTPException(413, f"recordings are limited to {cfg.upload_max_mb} MB")
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
                    raise HTTPException(413, f"recordings are limited to {cfg.upload_max_mb} MB")
                f.write(chunk)
        if size == 0:
            raise HTTPException(422, "the upload is empty")
        try:
            await decode_to_pcm(src, pcm, ffmpeg=cfg.ffmpeg_path)
        except UndecodableAudio as e:
            raise HTTPException(415, "no audio could be read from the file") from e
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
    )
    db.add(row)
    await db.commit()
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
    status: sl.Status | None = None,
    mode: sl.Mode | None = None,
    since: Annotated[datetime | None, Query(description="Started at or after this time.")] = None,
    sort: sl.Sort = "newest",
    q: Annotated[str | None, Query(max_length=sl.MAX_QUERY)] = None,
    q_in: Annotated[sl.SearchIn, Query(description="Search titles or transcripts.")] = "titles",
) -> SessionPage:
    """The caller's sessions, newest first by default. Admins may pass ``all=true`` for
    everyone's, or ``user_id`` for one user's; the scope applies before any filter or search.

    Filter by ``status``, ``mode`` and ``since``; ``sort`` is ``newest``, ``oldest`` or
    ``longest``. ``q`` searches titles, or with ``q_in=transcripts`` the final transcript
    lines (each hit then carries ``match``). ``counts`` is per status without the status
    filter."""
    if (all_users or user_id is not None) and not principal.is_admin:
        raise HTTPException(403, "admin only")
    scope = []
    if user_id is not None:
        scope.append(SessionRow.user_id == user_id)
    elif not all_users:
        scope.append(SessionRow.user_id == principal.user.id)
    term = sl.blank_to_none(q)
    narrowed = [*scope, *sl.filters(mode=mode, since=since, q=term, search_in=q_in)]
    where = [*narrowed, *([SessionRow.status == status] if status else [])]

    total = (
        await db.execute(select(func.count()).select_from(SessionRow).where(*where))
    ).scalar_one()
    status_rows = await db.execute(
        select(SessionRow.status, func.count()).where(*narrowed).group_by(SessionRow.status)
    )
    by_status = {st: n for st, n in status_rows.all()}
    counts = {st: by_status.get(st, 0) for st in sl.STATUSES}

    stmt = (
        select(SessionRow, User)
        .join(User, User.id == SessionRow.user_id)
        .where(*where)
        .order_by(*sl.order_by(sort))
        .limit(limit)
        .offset(offset)
    )
    items: list[SessionOut] = []
    if term and q_in == "transcripts":
        for row, owner, start_ms, snippet in (await db.execute(sl.with_match(stmt, term))).all():
            out = SessionOut.of(row, owner, request)
            out.match = MatchOut(start_ms=start_ms, snippet=snippet)
            items.append(out)
    else:
        items = [SessionOut.of(r, u, request) for r, u in (await db.execute(stmt)).all()]
    return SessionPage(items=items, total=total, limit=limit, offset=offset, counts=counts)


@router.get("/{session_id}")
async def get_session(
    session_id: uuid.UUID, principal: PrincipalDep, db: Db, request: Request
) -> SessionDetail:
    row, owner = await _visible(db, principal, session_id)
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
        can_publish=service.can_publish(principal.user) and may_publish_for(owner),
        can_regenerate=request.app.state.orchestrator is not None and not row.local_only,
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
            )
            for s in segments
        ],
        copilot=copilot,
    )


@router.post("/{session_id}/ticket")
async def issue_ticket(
    session_id: uuid.UUID, body: TicketRequest, principal: PrincipalDep, db: Db, request: Request
) -> TicketResponse:
    if body.role == "producer":
        if (await _owned(db, principal, session_id)).status != "live":
            raise HTTPException(409, "session is not live")
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
    row, owner = await _visible(db, principal, session_id)
    row.title = body.title
    await db.commit()
    session_logger(__name__, row.id).info("session renamed")
    return SessionOut.of(row, owner, request)


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
        raise HTTPException(404, "no such channel")
    return await wav_response(request, request.app.state.settings.data_dir, row.id, channel)
