"""Naming the voices of a session, and the voices a user has enrolled.

A voiceprint is biometric data: it is stored per user, listed here without the print itself, and
deleted on request. Naming a speaker once enrols their voice; later sessions recognise it
(see ``diarize/service.py``)."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_live.api.sessions import SpeakerOut
from jarvis_live.auth import Principal, current_principal
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.db.models import SessionSpeaker, SpeakerProfile
from jarvis_live.diarize.identify import merge
from jarvis_live.diarize.info import speaker_info
from jarvis_live.logging_setup import session_logger
from jarvis_live.protocol import SpeakerUpdate

router = APIRouter(prefix="/v1")

PrincipalDep = Annotated[Principal, Depends(current_principal)]


async def _db(request: Request) -> Any:
    async with request.app.state.sessionmaker() as db:
        yield db


Db = Annotated[AsyncSession, Depends(_db)]


class NameSpeaker(BaseModel):
    label: str = Field(max_length=100)
    # Also mark this voice as the user's own, whose lines read "me". Never takes it back: to
    # forget a voice, delete its profile.
    is_owner: bool = False
    # Remember the voice for future sessions. Without a voiceprint (the diarizer sent none) the
    # name still applies to this session.
    enroll: bool = True

    @field_validator("label")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("label must not be blank")
        return v


class ProfileOut(BaseModel):
    id: uuid.UUID
    label: str
    is_owner: bool
    n_samples: int
    model_id: str
    created_at: datetime


def _out(sp: SessionSpeaker) -> SpeakerOut:
    return SpeakerOut(
        id=sp.speaker_id,
        label=sp.label,
        ordinal=sp.ordinal,
        enrolled=sp.profile_id is not None,
        is_owner=sp.is_owner,
    )


@router.put("/sessions/{session_id}/speakers/{speaker_id}")
async def name_speaker(
    session_id: uuid.UUID,
    speaker_id: str,
    body: NameSpeaker,
    principal: PrincipalDep,
    db: Db,
    request: Request,
) -> SpeakerOut:
    row = await db.get(SessionRow, session_id)
    if row is None or row.user_id != principal.user.id:
        raise HTTPException(404, "Session not found.")
    sp = await db.get(SessionSpeaker, (session_id, speaker_id))
    if sp is None:
        raise HTTPException(404, "Speaker not found.")
    sp.label = body.label
    profile: SpeakerProfile | None = None
    if body.enroll and sp.embedding and sp.model_id:
        profile = (
            await db.execute(
                select(SpeakerProfile).where(
                    SpeakerProfile.user_id == principal.user.id,
                    SpeakerProfile.label == body.label,
                )
            )
        ).scalar_one_or_none()
        if profile is None:
            profile = SpeakerProfile(
                user_id=principal.user.id,
                label=body.label,
                model_id=sp.model_id,
                embedding=sp.embedding,
                n_samples=1,
            )
            db.add(profile)
        elif profile.model_id == sp.model_id:
            profile.embedding = merge(profile.embedding, profile.n_samples, sp.embedding, 1)
            profile.n_samples += 1
        else:  # the old print is from another model and cannot be compared: start over
            profile.model_id, profile.embedding, profile.n_samples = sp.model_id, sp.embedding, 1
        if body.is_owner and not profile.is_owner:
            await db.execute(  # one owner per user
                update(SpeakerProfile)
                .where(SpeakerProfile.user_id == principal.user.id, SpeakerProfile.is_owner)
                .values(is_owner=False)
            )
            profile.is_owner = True
        await db.flush()
        sp.profile_id = profile.id
        sp.is_owner = profile.is_owner
    elif body.is_owner:
        sp.is_owner = True
    changed = [sp]
    if sp.is_owner:  # only voices of the same person may be "me" in a session
        others = (
            (
                await db.execute(
                    select(SessionSpeaker).where(
                        SessionSpeaker.session_id == session_id,
                        SessionSpeaker.speaker_id != speaker_id,
                        SessionSpeaker.is_owner,
                    )
                )
            )
            .scalars()
            .all()
        )
        for o in others:
            if o.profile_id is None or o.profile_id != sp.profile_id:
                o.is_owner = False
                changed.append(o)
    await db.commit()
    request.app.state.bus.publish(
        str(session_id),
        SpeakerUpdate(
            speakers=[speaker_info(s) for s in changed],
            assignments=[],
        ),
    )
    session_logger(__name__, session_id).info(
        "speaker named", extra={"speaker": speaker_id, "enrolled": profile is not None}
    )
    return _out(sp)


@router.get("/speaker-profiles")
async def list_profiles(principal: PrincipalDep, db: Db) -> list[ProfileOut]:
    rows = (
        (
            await db.execute(
                select(SpeakerProfile)
                .where(SpeakerProfile.user_id == principal.user.id)
                .order_by(SpeakerProfile.label)
            )
        )
        .scalars()
        .all()
    )
    return [
        ProfileOut(
            id=p.id,
            label=p.label,
            is_owner=p.is_owner,
            n_samples=p.n_samples,
            model_id=p.model_id,
            created_at=p.created_at,
        )
        for p in rows
    ]


@router.delete("/speaker-profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: uuid.UUID, principal: PrincipalDep, db: Db) -> Response:
    """Forget a voice. Past sessions keep the name it was given; they just stop being linked."""
    p = await db.get(SpeakerProfile, profile_id)
    if p is None or p.user_id != principal.user.id:
        raise HTTPException(404, "Profile not found.")
    await db.delete(p)
    await db.commit()
    return Response(status_code=204)
