import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_live.auth import current_device
from jarvis_live.db.models import CopilotItem, Device, MarkerRow, SegmentRow
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

DeviceDep = Annotated[Device, Depends(current_device)]


async def _db(request: Request) -> Any:
    async with request.app.state.sessionmaker() as db:
        yield db


Db = Annotated[AsyncSession, Depends(_db)]


class SessionOut(BaseModel):
    id: uuid.UUID
    title: str | None
    mode: str
    status: str
    channels: list[str]
    started_at: datetime
    ended_at: datetime | None
    local_only: bool

    @classmethod
    def of(cls, s: SessionRow) -> "SessionOut":
        return cls(
            id=s.id,
            title=s.title,
            mode=s.mode,
            status=s.status,
            channels=list(s.channels),
            started_at=s.started_at,
            ended_at=s.ended_at,
            local_only=s.local_only,
        )


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


def _read_note(outbox: Path, name: str) -> str | None:
    # ``name`` is a bare file name written by the finalizer; refuse anything path-like.
    if not name or Path(name).name != name:
        return None
    try:
        return (outbox / name).read_text(encoding="utf-8")
    except OSError:
        return None


async def _owned(db: AsyncSession, device: Device, session_id: uuid.UUID) -> SessionRow:
    row = await db.get(SessionRow, session_id)
    if row is None or row.device_id != device.id:
        raise HTTPException(404, "session not found")
    return row


@router.post("", status_code=201)
async def create_session(
    body: SessionCreateRequest, device: DeviceDep, db: Db
) -> SessionCreateResponse:
    row = SessionRow(
        device_id=device.id,
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
    device: DeviceDep,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SessionPage:
    total = (
        await db.execute(
            select(func.count()).select_from(SessionRow).where(SessionRow.device_id == device.id)
        )
    ).scalar_one()
    rows = (
        (
            await db.execute(
                select(SessionRow)
                .where(SessionRow.device_id == device.id)
                .order_by(SessionRow.started_at.desc(), SessionRow.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return SessionPage(
        items=[SessionOut.of(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{session_id}")
async def get_session(
    session_id: uuid.UUID, device: DeviceDep, db: Db, request: Request
) -> SessionDetail:
    row = await _owned(db, device, session_id)
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
    return SessionDetail(
        **SessionOut.of(row).model_dump(),
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
    session_id: uuid.UUID, body: TicketRequest, device: DeviceDep, db: Db, request: Request
) -> TicketResponse:
    row = await _owned(db, device, session_id)
    if body.role == "producer" and row.status != "live":
        raise HTTPException(409, "session is not live")
    ticket = request.app.state.tickets.issue(session_id, device.id, body.role)
    return TicketResponse(ticket=ticket, expires_in=60)


@router.post("/{session_id}/end")
async def end_session(
    session_id: uuid.UUID, device: DeviceDep, db: Db, request: Request
) -> SessionOut:
    await _owned(db, device, session_id)
    await request.app.state.hub.end(session_id)
    row = await db.get(SessionRow, session_id, populate_existing=True)
    assert row is not None
    return SessionOut.of(row)
