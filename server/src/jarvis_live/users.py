"""Users: created at their first Pocket-ID sign-in, or by tests and the CLI."""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.config import Settings
from jarvis_live.db.models import (
    OWNER_USER_ID,
    DeviceCode,
    LoginSession,
    User,
)
from jarvis_live.db.models import Device as DeviceRow
from jarvis_live.db.models import Session as SessionRow

_SLUG_UNSAFE = re.compile(r"[^a-z0-9._-]+")


class SignInDenied(Exception):
    """The identity provider vouched for the user, but this deployment does not let them in."""

    def __init__(self, reason: str, account: str) -> None:
        super().__init__(reason)
        self.account = account  # email or name, so the user can tell which account was refused


@dataclass(frozen=True)
class Claims:
    sub: str
    email: str | None
    email_verified: bool
    name: str
    username: str | None
    groups: frozenset[str]

    @classmethod
    def of(cls, raw: dict[str, Any]) -> "Claims":
        email = raw.get("email") or None
        username = raw.get("preferred_username") or None
        groups = raw.get("groups") or []
        name = raw.get("name") or username or email or str(raw["sub"])
        return cls(
            sub=str(raw["sub"]),
            email=str(email) if email else None,
            email_verified=raw.get("email_verified") is not False,
            name=str(name),
            username=str(username) if username else None,
            groups=frozenset(str(g) for g in groups if isinstance(g, str)),
        )


def slug(value: str) -> str:
    """A safe single path segment for a user's notes folder."""
    s = _SLUG_UNSAFE.sub("-", value.lower()).strip("-.")
    return s[:40] or "user"


async def _free_subdir(db: AsyncSession, base: str) -> str:
    taken = set(
        (await db.execute(select(User.notes_subdir).where(User.notes_subdir.isnot(None)))).scalars()
    )
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}"
    return candidate


async def create_user(
    sessionmaker: "async_sessionmaker[AsyncSession]",
    display_name: str,
    *,
    email: str | None = None,
    oidc_sub: str | None = None,
    is_admin: bool = False,
) -> User:
    async with sessionmaker() as db:
        user = User(
            display_name=display_name,
            email=email,
            oidc_sub=oidc_sub,
            is_admin=is_admin,
            notes_subdir=await _free_subdir(
                db, slug(display_name if email is None else email.split("@")[0])
            ),
        )
        db.add(user)
        await db.commit()
    return user


async def sign_in(
    sessionmaker: "async_sessionmaker[AsyncSession]", claims: Claims, settings: Settings
) -> User:
    """Find or create the user for verified ID-token claims and refresh their profile."""
    allowed = {g.strip() for g in settings.oidc_allowed_groups.split(",") if g.strip()}
    if allowed and not allowed & claims.groups:
        raise SignInDenied(
            f"groups {sorted(claims.groups)} include none of {sorted(allowed)}",
            account=claims.email or claims.name,
        )
    now = datetime.now(UTC)
    async with sessionmaker() as db:
        user = (
            await db.execute(select(User).where(User.oidc_sub == claims.sub))
        ).scalar_one_or_none()
        if user is None and _is_owner_email(claims, settings):
            owner = await db.get(User, OWNER_USER_ID)
            if owner is not None and owner.oidc_sub is None:
                user = owner
                user.oidc_sub = claims.sub
        if user is None:
            base = claims.username or (claims.email.split("@")[0] if claims.email else claims.name)
            user = User(
                oidc_sub=claims.sub,
                display_name=claims.name,
                notes_subdir=await _free_subdir(db, slug(base)),
            )
            db.add(user)
        user.email = claims.email
        user.display_name = claims.name
        user.last_login_at = now
        if settings.oidc_admin_group:
            # The group decides, except that the owner is always an admin.
            user.is_admin = settings.oidc_admin_group in claims.groups or user.id == OWNER_USER_ID
        await db.commit()
    return user


def _is_owner_email(claims: Claims, settings: Settings) -> bool:
    return bool(
        settings.owner_email
        and claims.email
        and claims.email_verified
        and claims.email.lower() == settings.owner_email.strip().lower()
    )


async def claim_owner(sessionmaker: "async_sessionmaker[AsyncSession]", email: str) -> User:
    """Hand the owner account to the user who signed in with ``email``: their devices and
    sessions move to the owner, and their own (now empty) account is removed."""
    async with sessionmaker() as db:
        found = (
            (await db.execute(select(User).where(func.lower(User.email) == email.lower())))
            .scalars()
            .all()
        )
        if len(found) != 1:
            raise LookupError(f"{len(found)} users have the email {email!r}; sign in first")
        other = found[0]
        owner = await db.get(User, OWNER_USER_ID)
        if owner is None:
            raise LookupError("no owner account (run the migrations)")
        if other.id == owner.id:
            return owner
        for model in (DeviceRow, SessionRow, LoginSession, DeviceCode):
            await db.execute(
                update(model).where(model.user_id == other.id).values(user_id=owner.id)
            )
        sub, mail, name, last = other.oidc_sub, other.email, other.display_name, other.last_login_at
        await db.execute(delete(User).where(User.id == other.id))
        await db.flush()
        owner.oidc_sub, owner.email, owner.display_name = sub, mail, name
        owner.last_login_at = last
        await db.commit()
    return owner


async def find_user(db: AsyncSession, ref: str) -> User | None:
    """A user by id or (case-insensitive) email."""
    try:
        return await db.get(User, uuid.UUID(ref))
    except ValueError:
        pass
    return (
        await db.execute(select(User).where(func.lower(User.email) == ref.lower()))
    ).scalar_one_or_none()
