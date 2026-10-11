"""The signed-in user, their devices, and (for admins) everyone."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_live.auth import Principal, create_device, current_principal, require_admin
from jarvis_live.db.models import Device, User
from jarvis_live.db.models import Session as SessionRow

router = APIRouter(prefix="/v1")

PrincipalDep = Annotated[Principal, Depends(current_principal)]
AdminDep = Annotated[Principal, Depends(require_admin)]


async def _db(request: Request) -> Any:
    async with request.app.state.sessionmaker() as db:
        yield db


Db = Annotated[AsyncSession, Depends(_db)]


class MeOut(BaseModel):
    id: uuid.UUID
    email: str | None
    display_name: str
    is_admin: bool
    device_id: uuid.UUID | None  # set when calling with a device token
    can_publish: bool  # may publish notes to the vault repo (owner and admins, if configured)
    auto_publish: bool  # new notes open their PR without asking


def _me(principal: Principal, request: Request) -> MeOut:
    u = principal.user
    return MeOut(
        id=u.id,
        email=u.email,
        display_name=u.display_name,
        is_admin=u.is_admin,
        device_id=principal.device.id if principal.device else None,
        can_publish=request.app.state.publishing.can_publish(u),
        auto_publish=u.auto_publish,
    )


@router.get("/me")
async def me(principal: PrincipalDep, request: Request) -> MeOut:
    return _me(principal, request)


class MeUpdate(BaseModel):
    auto_publish: bool


@router.patch("/me")
async def update_me(body: MeUpdate, principal: PrincipalDep, db: Db, request: Request) -> MeOut:
    if body.auto_publish and not request.app.state.publishing.can_publish(principal.user):
        raise HTTPException(403, "only the owner and admins publish notes")
    user = await db.get(User, principal.user.id)
    assert user is not None
    user.auto_publish = body.auto_publish
    await db.commit()
    principal.user.auto_publish = body.auto_publish
    return _me(principal, request)


class DeviceOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    revoked_at: datetime | None
    current: bool  # the device making this request


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class DeviceCreated(DeviceOut):
    token: str  # shown once


@router.get("/devices")
async def list_devices(principal: PrincipalDep, db: Db) -> list[DeviceOut]:
    rows = (
        (
            await db.execute(
                select(Device)
                .where(Device.user_id == principal.user.id)
                .order_by(Device.revoked_at.is_not(None), Device.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    current = principal.device.id if principal.device else None
    return [
        DeviceOut(
            id=d.id,
            name=d.name,
            created_at=d.created_at,
            revoked_at=d.revoked_at,
            current=d.id == current,
        )
        for d in rows
    ]


@router.post("/devices", status_code=201)
async def add_device(
    body: DeviceCreate, principal: PrincipalDep, db: Db, request: Request
) -> DeviceCreated:
    """New device token for e.g. the macOS app. Only from a signed-in browser: a device token
    cannot mint more tokens."""
    if principal.device is not None:
        raise HTTPException(403, "sign in on the web to add devices")
    device_id, token = await create_device(
        request.app.state.sessionmaker, body.name.strip(), principal.user.id
    )
    d = await db.get(Device, device_id)
    assert d is not None
    return DeviceCreated(
        id=d.id, name=d.name, created_at=d.created_at, revoked_at=None, current=False, token=token
    )


@router.delete("/devices/{device_id}", status_code=204)
async def revoke_device(device_id: uuid.UUID, principal: PrincipalDep, db: Db) -> None:
    d = await db.get(Device, device_id)
    if d is None or d.user_id != principal.user.id:
        raise HTTPException(404, "device not found")
    if d.revoked_at is None:
        d.revoked_at = func.now()
        await db.commit()


class UserOut(BaseModel):
    id: uuid.UUID
    email: str | None
    display_name: str
    is_admin: bool
    created_at: datetime
    last_login_at: datetime | None
    sessions: int


@router.get("/admin/users")
async def list_users(_: AdminDep, db: Db) -> list[UserOut]:
    counts = (
        select(SessionRow.user_id, func.count().label("n")).group_by(SessionRow.user_id).subquery()
    )
    rows = (
        await db.execute(
            select(User, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.user_id == User.id)
            .order_by(User.display_name, User.id)
        )
    ).all()
    return [
        UserOut(
            id=u.id,
            email=u.email,
            display_name=u.display_name,
            is_admin=u.is_admin,
            created_at=u.created_at,
            last_login_at=u.last_login_at,
            sessions=n,
        )
        for u, n in rows
    ]
