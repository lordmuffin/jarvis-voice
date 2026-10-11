import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_live.api.audio import audio_available, wav_response
from jarvis_live.auth import Principal, current_principal
from jarvis_live.db.models import CopilotItem, MarkerRow, SegmentRow, User
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.logging_setup import session_logger
from jarvis_live.protocol import (
    Copilot,
    SessionCreateRequest,
    SessionCreateResponse,
    TicketRequest,
    TicketResponse,
)

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
    # Audio is arriving right now. ``status`` stays ``live`` while a dropped producer may still
    # reconnect, so viewers use this to tell "live" from "live but nothing streaming".
    streaming: bool
    owner: OwnerOut

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


class SessionDetail(SessionOut):
    segments: list[SegmentOut]
    copilot: Copilot
    markers: list[MarkerOut]
    final_note: FinalNoteOut | None
    # Which channels' recordings can be played (``GET .../audio/{channel}.wav``).
    audio: dict[str, bool]


def _read_note(outbox: Path, name: str) -> str | None:
    # ``name`` is what the finalizer wrote: a file name, optionally under the user's notes
    # folder. Refuse anything else.
    parts = Path(name).parts
    if not name or not 1 <= len(parts) <= 2 or any(p in {"", ".", ".."} for p in parts):
        return None
    if Path(name).is_absolute():
        return None
    try:
        return (outbox / name).read_text(encoding="utf-8")
    except OSError:
        return None


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


@router.get("")
async def list_sessions(
    principal: PrincipalDep,
    db: Db,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    all_users: Annotated[bool, Query(alias="all")] = False,
    user_id: uuid.UUID | None = None,
) -> SessionPage:
    """The caller's sessions, newest first. Admins may pass ``all=true`` for everyone's, or
    ``user_id`` for one user's."""
    if (all_users or user_id is not None) and not principal.is_admin:
        raise HTTPException(403, "admin only")
    where = []
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
            .order_by(SessionRow.started_at.desc(), SessionRow.id.desc())
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
    final_payload = (
        await db.execute(
            select(CopilotItem.payload)
            .where(CopilotItem.session_id == session_id, CopilotItem.kind == "final_note")
            .order_by(CopilotItem.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    final_note: FinalNoteOut | None = None
    if final_payload is not None:
        cfg = request.app.state.settings
        outbox = cfg.outbox_dir or cfg.data_dir / "outbox"
        path = str(final_payload["path"])
        markdown = await asyncio.to_thread(_read_note, outbox, path)
        final_note = FinalNoteOut(path=path, title=str(final_payload["title"]), markdown=markdown)
    data_dir = request.app.state.settings.data_dir
    audio = {c: await asyncio.to_thread(audio_available, data_dir, row.id, c) for c in row.channels}
    return SessionDetail(
        **SessionOut.of(row, owner, request).model_dump(),
        audio=audio,
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
