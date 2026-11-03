"""Ingest keys: the credential a pushed stream publishes with.

A key belongs to one path (its ``name``), may only publish, and carries the profile of the
sessions its streams start (mode, title, restricted, source). It is shown once on creation; only
its sha256 is stored, like a device token. It is a separate credential so a device token, which
can read everything its user can, never has to be typed into OBS or a phone app.
"""

import hmac
import re
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.auth import hash_token
from jarvis_live.db.models import IngestKey

MODES = ("meeting", "solo")
SOURCES = ("obs", "mobile")
# A MediaMTX path, an SRT streamid part and an RTMP app path all at once: nothing to escape.
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")

SM = async_sessionmaker[AsyncSession]


class IngestKeyError(ValueError):
    pass


def check_name(name: str) -> str:
    if not _NAME.fullmatch(name):
        raise IngestKeyError(
            f"{name!r} is not a valid stream name: 1-40 of a-z, 0-9 and '-', not starting with '-'"
        )
    return name


async def create_key(
    sm: SM,
    name: str,
    user_id: uuid.UUID,
    *,
    mode: str = "solo",
    title: str | None = None,
    restricted: bool = False,
    source: str = "obs",
) -> str:
    """Create the key for path ``name`` and return it. It is not stored and not shown again."""
    check_name(name)
    if mode not in MODES:
        raise IngestKeyError(f"mode must be one of {', '.join(MODES)}")
    if source not in SOURCES:
        raise IngestKeyError(f"source must be one of {', '.join(SOURCES)}")
    secret = secrets.token_urlsafe(32)
    row = IngestKey(
        user_id=user_id,
        name=name,
        key_hash=hash_token(secret),
        mode=mode,
        title=(title or "").strip() or None,
        restricted=restricted,
        source=source,
    )
    async with sm() as db:
        db.add(row)
        try:
            await db.commit()
        except IntegrityError as e:
            raise IngestKeyError(f"a stream named {name!r} already exists") from e
    return secret


async def active_key(sm: SM, name: str) -> IngestKey | None:
    """The unrevoked key of path ``name``."""
    async with sm() as db:
        return (
            await db.execute(
                select(IngestKey).where(IngestKey.name == name, IngestKey.revoked_at.is_(None))
            )
        ).scalar_one_or_none()


async def verify(sm: SM, name: str, secret: str) -> IngestKey | None:
    """The key of path ``name`` if ``secret`` is it and it is not revoked."""
    if not secret:
        return None
    key = await active_key(sm, name)
    if key is None or not hmac.compare_digest(key.key_hash, hash_token(secret)):
        return None
    return key


async def revoke_key(sm: SM, name: str) -> bool:
    """Revoke path ``name``'s key. False if there was no unrevoked one."""
    async with sm() as db:
        result = await db.execute(
            update(IngestKey)
            .where(IngestKey.name == name, IngestKey.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await db.commit()
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def list_keys(sm: SM) -> list[IngestKey]:
    async with sm() as db:
        return list((await db.execute(select(IngestKey).order_by(IngestKey.name))).scalars())
