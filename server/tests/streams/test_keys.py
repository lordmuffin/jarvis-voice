"""Ingest keys: the publish-only credential a stream pushes with, and the session profile it
carries. Only the sha256 of a key is stored."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.db.models import OWNER_USER_ID, IngestKey
from jarvis_live.streams import keys

SM = async_sessionmaker  # type: ignore[type-arg]
RUN = uuid.uuid4().hex[:6]  # the test database outlives one test: names must not repeat
N = {
    n: f"{n}-{RUN}"
    for n in [
        "reuse",
        "rot",
        "rot-none",
        "race",
        "obs-desk",
        "cam-a",
        "cam-b",
        "cam-c",
        "phone-x",
        "dup",
        "m1",
        "m2",
    ]
}


@pytest.mark.parametrize("name", [N["obs-desk"], "phone1", "a"])
def test_accepts_path_safe_names(name: str) -> None:
    assert keys.check_name(name) == name


@pytest.mark.parametrize("name", ["", "Obs", "-x", "a/b", "a b", "x" * 41, "obs_desk", "a?b"])
def test_rejects_names_that_are_not_a_plain_path(name: str) -> None:
    with pytest.raises(keys.IngestKeyError):
        keys.check_name(name)


async def test_a_created_key_verifies_and_only_its_hash_is_stored(sm: SM) -> None:
    secret = await keys.create_key(sm, N["obs-desk"], OWNER_USER_ID, mode="meeting", title="Stream")
    found = await keys.verify(sm, N["obs-desk"], secret)
    assert found is not None
    assert (found.mode, found.title, found.restricted, found.source) == (
        "meeting",
        "Stream",
        False,
        "obs",
    )
    async with sm() as db:
        row = (
            await db.execute(select(IngestKey).where(IngestKey.name == N["obs-desk"]))
        ).scalar_one()
    assert secret not in row.key_hash and len(row.key_hash) == 64


async def test_a_wrong_secret_or_another_path_does_not_verify(sm: SM) -> None:
    secret = await keys.create_key(sm, N["cam-a"], OWNER_USER_ID)
    await keys.create_key(sm, N["cam-b"], OWNER_USER_ID)
    assert await keys.verify(sm, N["cam-a"], "nope") is None
    assert await keys.verify(sm, N["cam-b"], secret) is None  # a key publishes to its own path only
    assert await keys.verify(sm, N["cam-c"], secret) is None
    assert await keys.verify(sm, N["cam-a"], "") is None


async def test_a_revoked_key_no_longer_verifies(sm: SM) -> None:
    secret = await keys.create_key(sm, N["phone-x"], OWNER_USER_ID, source="mobile")
    assert await keys.revoke_key(sm, N["phone-x"]) is True
    assert await keys.verify(sm, N["phone-x"], secret) is None
    assert await keys.active_key(sm, N["phone-x"]) is None
    assert await keys.revoke_key(sm, N["phone-x"]) is False  # already revoked


async def test_names_are_unique(sm: SM) -> None:
    await keys.create_key(sm, N["dup"], OWNER_USER_ID)
    with pytest.raises(keys.IngestKeyError):
        await keys.create_key(sm, N["dup"], OWNER_USER_ID)


async def test_rejects_an_unknown_mode_or_source(sm: SM) -> None:
    with pytest.raises(keys.IngestKeyError):
        await keys.create_key(sm, N["m1"], OWNER_USER_ID, mode="party")
    with pytest.raises(keys.IngestKeyError):
        await keys.create_key(sm, N["m2"], OWNER_USER_ID, source="watch")


async def test_a_revoked_name_can_be_created_again(sm: SM) -> None:
    old = await keys.create_key(sm, N["reuse"], OWNER_USER_ID)
    await keys.revoke_key(sm, N["reuse"])
    new = await keys.create_key(sm, N["reuse"], OWNER_USER_ID, mode="meeting")
    assert await keys.verify(sm, N["reuse"], old) is None
    found = await keys.verify(sm, N["reuse"], new)
    assert found is not None and found.mode == "meeting"


async def test_rotate_issues_a_new_key_kills_the_old_and_keeps_the_profile(sm: SM) -> None:
    old = await keys.create_key(
        sm, N["rot"], OWNER_USER_ID, mode="meeting", title="Desk", restricted=True, source="mobile"
    )
    new = await keys.rotate_key(sm, N["rot"])
    assert new != old
    assert await keys.verify(sm, N["rot"], old) is None
    found = await keys.verify(sm, N["rot"], new)
    assert found is not None
    assert (found.user_id, found.mode, found.title, found.restricted, found.source) == (
        OWNER_USER_ID,
        "meeting",
        "Desk",
        True,
        "mobile",
    )
    listed = [k for k in await keys.list_keys(sm) if k.name == N["rot"]]
    assert sorted(k.revoked_at is None for k in listed) == [False, True]  # history kept


async def test_rotating_a_name_without_an_active_key_fails(sm: SM) -> None:
    with pytest.raises(keys.IngestKeyError):
        await keys.rotate_key(sm, N["rot-none"])
    await keys.create_key(sm, N["rot-none"], OWNER_USER_ID)
    await keys.revoke_key(sm, N["rot-none"])
    with pytest.raises(keys.IngestKeyError):
        await keys.rotate_key(sm, N["rot-none"])


async def test_the_database_allows_one_active_key_per_name(sm: SM) -> None:
    """Even past the code's own checks (two creates racing): a partial unique index."""
    await keys.create_key(sm, N["race"], OWNER_USER_ID)
    async with sm() as db:
        db.add(
            IngestKey(
                user_id=OWNER_USER_ID, name=N["race"], key_hash="0" * 64, mode="solo", source="obs"
            )
        )
        with pytest.raises(IntegrityError):
            await db.commit()
