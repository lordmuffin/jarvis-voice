import hashlib
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.db.models import Device

Role = Literal["producer", "viewer"]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_device(
    sessionmaker: "async_sessionmaker[AsyncSession]", name: str
) -> tuple[uuid.UUID, str]:
    """Create a device and return ``(id, token)``. The token is never stored, only its sha256."""
    token = secrets.token_urlsafe(32)
    device = Device(name=name, token_hash=hash_token(token))
    async with sessionmaker() as db:
        db.add(device)
        await db.commit()
    return device.id, token


@dataclass(frozen=True)
class Ticket:
    session_id: uuid.UUID
    device_id: uuid.UUID
    role: Role
    expires_at: float


class TicketStore:
    """Single-use, short-lived WebSocket tickets. In-memory: valid for a single replica."""

    def __init__(self, ttl_s: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_s
        self._clock = clock
        self._tickets: dict[str, Ticket] = {}

    def issue(self, session_id: uuid.UUID, device_id: uuid.UUID, role: Role) -> str:
        self._purge()
        value = secrets.token_urlsafe(24)
        self._tickets[value] = Ticket(session_id, device_id, role, self._clock() + self._ttl)
        return value

    def redeem(self, value: str, session_id: uuid.UUID) -> Ticket | None:
        """Consume a ticket. A ticket for another session is consumed too (and rejected)."""
        ticket = self._tickets.pop(value, None)
        if ticket is None or ticket.expires_at <= self._clock() or ticket.session_id != session_id:
            return None
        return ticket

    def _purge(self) -> None:
        now = self._clock()
        for k in [k for k, t in self._tickets.items() if t.expires_at <= now]:
            del self._tickets[k]


_bearer = HTTPBearer(auto_error=False)


async def current_device(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Device:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    async with request.app.state.sessionmaker() as db:
        device = (
            await db.execute(
                select(Device).where(Device.token_hash == hash_token(creds.credentials))
            )
        ).scalar_one_or_none()
    if device is None or device.revoked_at is not None:
        raise HTTPException(401, "invalid or revoked token", headers={"WWW-Authenticate": "Bearer"})
    assert isinstance(device, Device)
    return device
