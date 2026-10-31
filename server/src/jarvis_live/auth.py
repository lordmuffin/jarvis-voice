import hashlib
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.config import Settings
from jarvis_live.db.models import OWNER_USER_ID, Device, LoginSession, User

Role = Literal["producer", "viewer"]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_device_token() -> str:
    """A url-safe device token that never starts with "-".

    A leading dash makes argparse treat ``--token <token>`` as a missing value. Rejection
    sampling keeps the length and costs log2(64/63) ~ 0.02 bits of entropy.
    """
    while True:
        token = secrets.token_urlsafe(32)
        if not token.startswith("-"):
            return token


async def create_device(
    sessionmaker: "async_sessionmaker[AsyncSession]", name: str, user_id: uuid.UUID
) -> tuple[uuid.UUID, str]:
    """Create a device for a user and return ``(id, token)``. The token is never stored, only
    its sha256."""
    token = new_device_token()
    device = Device(name=name, token_hash=hash_token(token), user_id=user_id)
    async with sessionmaker() as db:
        db.add(device)
        await db.commit()
    return device.id, token


@dataclass(frozen=True)
class Ticket:
    session_id: uuid.UUID
    user_id: uuid.UUID
    role: Role
    expires_at: float


class TicketStore:
    """Single-use, short-lived WebSocket tickets. In-memory: valid for a single replica."""

    def __init__(self, ttl_s: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_s
        self._clock = clock
        self._tickets: dict[str, Ticket] = {}

    def issue(self, session_id: uuid.UUID, user_id: uuid.UUID, role: Role) -> str:
        self._purge()
        value = secrets.token_urlsafe(24)
        self._tickets[value] = Ticket(session_id, user_id, role, self._clock() + self._ttl)
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


SESSION_COOKIE = "jarvis_live_session"
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass(frozen=True)
class Principal:
    """Who is calling: a user, through one of their devices (bearer token) or through the web
    dashboard (session cookie, ``device`` is None)."""

    user: User
    device: Device | None

    @property
    def is_admin(self) -> bool:
        return self.user.is_admin


async def create_login_session(
    sessionmaker: "async_sessionmaker[AsyncSession]", user_id: uuid.UUID, days: int
) -> str:
    """Start a web sign-in; returns the cookie value (only its sha256 is stored)."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    async with sessionmaker() as db:
        db.add(
            LoginSession(
                token_hash=hash_token(token), user_id=user_id, expires_at=now + timedelta(days=days)
            )
        )
        await db.commit()
    return token


def set_session_cookie(response: Response, value: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        value,
        max_age=settings.login_session_days * 86_400,
        httponly=True,
        secure=settings.public_base_url.startswith("https://"),
        samesite="lax",
        path="/",
    )


def _same_origin(request: Request) -> bool:
    """CSRF guard for cookie-authenticated writes: browsers always send ``Origin`` on
    cross-origin and same-origin POST/PATCH/DELETE fetches."""
    origin = request.headers.get("origin")
    if not origin:
        return False
    netloc = urlsplit(origin).netloc
    public = request.app.state.settings.public_base_url
    return netloc == request.headers.get("host") or bool(
        public and netloc == urlsplit(public).netloc
    )


_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


async def current_principal(
    request: Request,
    response: Response,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if creds is not None and creds.scheme.lower() == "bearer":
        async with request.app.state.sessionmaker() as db:
            found = (
                await db.execute(
                    select(Device, User)
                    .join(User, User.id == Device.user_id)
                    .where(Device.token_hash == hash_token(creds.credentials))
                )
            ).one_or_none()
        if found is None or found[0].revoked_at is not None:
            raise _unauthorized("Invalid or revoked token.")
        return Principal(user=found[1], device=found[0])

    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        raise _unauthorized("Not signed in.")
    now = datetime.now(UTC)
    async with request.app.state.sessionmaker() as db:
        found = (
            await db.execute(
                select(LoginSession, User)
                .join(User, User.id == LoginSession.user_id)
                .where(LoginSession.token_hash == hash_token(cookie))
            )
        ).one_or_none()
        if found is None or found[0].expires_at <= now:
            raise _unauthorized("Not signed in.")
        login, user = found
        # Sliding expiry: a dashboard used at least every few weeks stays signed in.
        settings = request.app.state.settings
        days = settings.login_session_days
        if login.expires_at - now < timedelta(days=days / 2):
            await db.execute(
                update(LoginSession)
                .where(LoginSession.id == login.id)
                .values(expires_at=now + timedelta(days=days))
            )
            await db.commit()
            set_session_cookie(response, cookie, settings)
    if request.method in _UNSAFE_METHODS and not _same_origin(request):
        raise HTTPException(403, "Cross-origin request.")
    return Principal(user=user, device=None)


async def require_admin(
    principal: Annotated[Principal, Depends(current_principal)],
) -> Principal:
    if not principal.is_admin:
        raise HTTPException(403, "Admin only.")
    return principal


async def require_owner_or_admin(
    principal: Annotated[Principal, Depends(current_principal)],
) -> Principal:
    """The deployment owner and admins: whoever may use the homelab and the vault."""
    if principal.user.id != OWNER_USER_ID and not principal.is_admin:
        raise HTTPException(403, "Only the owner and admins can do this.")
    return principal
