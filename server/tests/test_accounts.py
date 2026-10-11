"""Sign-in with (a fake) Pocket-ID, web cookies, Android device codes, devices, the owner
account, and the 0002 migration."""

import asyncio
import base64
import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import asyncpg
import httpx
import pytest
from alembic import command
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.api.auth import ANDROID_REDIRECT
from jarvis_live.db.models import OWNER_USER_ID, Device, DeviceCode, User
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.db.session import alembic_config
from jarvis_live.users import claim_owner, create_user
from tests.conftest import ServerHandle
from tests.fake_oidc import FakeIssuer
from tests.fakes import make_session, new_device

SM = async_sessionmaker  # type: ignore[type-arg]


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def login(c: httpx.AsyncClient, sub: str, **params: str) -> httpx.Response:
    """Walk the redirects: /auth/login -> fake Pocket-ID (picking ``sub``) -> /auth/callback.
    Returns the callback's response."""
    r = await c.get("/auth/login", params=params)
    assert r.status_code == 303, r.text
    r = await c.get(r.headers["location"] + f"&user={sub}")
    assert r.status_code == 307, r.text
    return await c.get(r.headers["location"])


@pytest.fixture
def oidc_server(
    make_server: Callable[..., ServerHandle], oidc_settings: dict[str, Any]
) -> Callable[..., ServerHandle]:
    return lambda **kw: make_server(**{**oidc_settings, **kw})


# --- web ---------------------------------------------------------------------------------------


async def test_web_sign_in_sets_a_cookie_and_lands_on_return_to(
    oidc_server: Callable[..., ServerHandle],
) -> None:
    server = oidc_server()
    async with httpx.AsyncClient(base_url=server.url) as c:
        assert (await c.get("/v1/me")).status_code == 401
        r = await login(c, "alice", return_to="/sessions/abc")
        assert r.status_code == 303 and r.headers["location"] == "/sessions/abc"
        cookie = r.headers["set-cookie"].lower()
        assert (
            "jarvis_live_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
        )
        me = (await c.get("/v1/me")).json()
        assert (me["email"], me["display_name"], me["is_admin"], me["device_id"]) == (
            "alice@example.com",
            "Alice Example",
            False,
            None,
        )
        assert (await c.get("/v1/sessions")).status_code == 200

        # signing in again is the same user
        await login(c, "alice")
        assert (await c.get("/v1/me")).json()["id"] == me["id"]

        assert (await c.post("/auth/logout")).status_code == 204
        assert (await c.get("/v1/me")).status_code == 401


@pytest.mark.parametrize("target", ["//evil.example/x", "https://evil.example/", "\\\\evil"])
async def test_return_to_is_same_origin_only(
    oidc_server: Callable[..., ServerHandle], target: str
) -> None:
    async with httpx.AsyncClient(base_url=oidc_server().url) as c:
        r = await login(c, "alice", return_to=target)
        assert r.headers["location"] == "/"


async def test_admin_comes_from_the_admin_group(oidc_server: Callable[..., ServerHandle]) -> None:
    server = oidc_server()
    async with httpx.AsyncClient(base_url=server.url) as c:
        await login(c, "root")
        assert (await c.get("/v1/me")).json()["is_admin"] is True
        assert (await c.get("/v1/admin/users")).status_code == 200
        assert (await c.get("/v1/sessions", params={"all": "true"})).status_code == 200
    async with httpx.AsyncClient(base_url=server.url) as c:
        await login(c, "bob")
        assert (await c.get("/v1/admin/users")).status_code == 403


async def test_allowed_group_gates_sign_in(oidc_server: Callable[..., ServerHandle]) -> None:
    server = oidc_server(oidc_allowed_group="jarvis")
    async with httpx.AsyncClient(base_url=server.url) as c:
        r = await login(c, "mallory")
        assert r.status_code == 403 and "set-cookie" not in r.headers
        assert (await c.get("/v1/me")).status_code == 401
        assert (await login(c, "bob")).status_code == 303


async def test_bad_state_and_cancelled_sign_in(
    oidc_server: Callable[..., ServerHandle], issuer: FakeIssuer
) -> None:
    async with httpx.AsyncClient(base_url=oidc_server().url) as c:
        assert (
            await c.get("/auth/callback", params={"state": "nope", "code": "x"})
        ).status_code == 400
        r = await login(c, "deny")
        assert r.status_code == 400
        assert issuer.token_requests == 0


async def test_sign_in_is_off_without_an_issuer(server: ServerHandle) -> None:
    async with httpx.AsyncClient(base_url=server.url) as c:
        assert (await c.get("/auth/login")).status_code == 404


async def test_cookie_writes_need_a_same_origin_header(
    oidc_server: Callable[..., ServerHandle],
) -> None:
    server = oidc_server()
    async with httpx.AsyncClient(base_url=server.url) as c:
        await login(c, "alice")
        body = {"name": "Mac"}
        assert (await c.post("/v1/devices", json=body)).status_code == 403  # no Origin
        evil = {"Origin": "https://evil.example"}
        assert (await c.post("/v1/devices", json=body, headers=evil)).status_code == 403
        ok = {"Origin": server.url}
        assert (await c.post("/v1/devices", json=body, headers=ok)).status_code == 201
        assert (await c.get("/v1/devices")).status_code == 200  # reads need no Origin


async def test_devices_are_created_on_the_web_and_revocable(
    oidc_server: Callable[..., ServerHandle],
) -> None:
    server = oidc_server()
    origin = {"Origin": server.url}
    async with httpx.AsyncClient(base_url=server.url, headers=origin) as web:
        await login(web, "bob")
        me = (await web.get("/v1/me")).json()
        created = (await web.post("/v1/devices", json={"name": " Bob's Mac "})).json()
        assert created["name"] == "Bob's Mac" and created["token"]
        listed = (await web.get("/v1/devices")).json()
        assert created["id"] in [d["id"] for d in listed]
        assert all("token" not in d for d in listed)

        mac = {"Authorization": f"Bearer {created['token']}"}
        async with httpx.AsyncClient(base_url=server.url, headers=mac) as dev:
            dme = (await dev.get("/v1/me")).json()
            assert (dme["id"], dme["device_id"]) == (me["id"], created["id"])
            assert any(d["current"] for d in (await dev.get("/v1/devices")).json())
            # a device token cannot mint more tokens
            assert (await dev.post("/v1/devices", json={"name": "x"})).status_code == 403
            sid = (
                await dev.post("/v1/sessions", json={"mode": "solo", "channels": ["mic"]})
            ).json()["id"]
            # the web sees the device's sessions; it cannot record itself
            assert (await web.get(f"/v1/sessions/{sid}")).status_code == 200
            r = await web.post("/v1/sessions", json={"mode": "solo", "channels": ["mic"]})
            assert r.status_code == 403

            assert (await web.delete(f"/v1/devices/{created['id']}")).status_code == 204
            assert (await dev.get("/v1/me")).status_code == 401

        assert (await web.delete(f"/v1/devices/{uuid.uuid4()}")).status_code == 404


async def test_someone_elses_device_is_not_yours_to_revoke(
    oidc_server: Callable[..., ServerHandle], sm: SM
) -> None:
    server = oidc_server()
    device_id, token = await new_device(sm, "stranger's phone")
    async with httpx.AsyncClient(base_url=server.url, headers={"Origin": server.url}) as web:
        await login(web, "bob")
        assert (await web.delete(f"/v1/devices/{device_id}")).status_code == 404
    async with httpx.AsyncClient(
        base_url=server.url, headers={"Authorization": f"Bearer {token}"}
    ) as dev:
        assert (await dev.get("/v1/me")).status_code == 200


# --- android -----------------------------------------------------------------------------------


async def android_code(c: httpx.AsyncClient, sub: str, challenge: str) -> dict[str, list[str]]:
    r = await login(c, sub, client="android", challenge=challenge)
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith(ANDROID_REDIRECT + "?")
    assert "set-cookie" not in r.headers  # the app gets a device token, not a web session
    return parse_qs(urlsplit(location).query)


async def test_android_sign_in_hands_the_app_a_device_token(
    oidc_server: Callable[..., ServerHandle],
) -> None:
    server = oidc_server()
    verifier, challenge = pkce()
    async with httpx.AsyncClient(base_url=server.url) as c:
        code = (await android_code(c, "alice", challenge))["code"][0]
        body = {"code": code, "verifier": verifier, "device_name": "Pixel 9"}
        r = await c.post("/v1/auth/device-code", json=body)
        assert r.status_code == 200
        token = r.json()["token"]
        me = (await c.get("/v1/me", headers={"Authorization": f"Bearer {token}"})).json()
        assert me["email"] == "alice@example.com" and me["device_id"] == r.json()["device_id"]
        # one use only
        assert (await c.post("/v1/auth/device-code", json=body)).status_code == 400


async def test_android_code_needs_the_matching_verifier(
    oidc_server: Callable[..., ServerHandle],
) -> None:
    server = oidc_server()
    verifier, challenge = pkce()
    async with httpx.AsyncClient(base_url=server.url) as c:
        code = (await android_code(c, "alice", challenge))["code"][0]
        wrong = {"code": code, "verifier": pkce()[0], "device_name": "Pixel"}
        assert (await c.post("/v1/auth/device-code", json=wrong)).status_code == 400
        # the failed attempt used the code up
        right = {"code": code, "verifier": verifier, "device_name": "Pixel"}
        assert (await c.post("/v1/auth/device-code", json=right)).status_code == 400


async def test_android_code_expires(oidc_server: Callable[..., ServerHandle], sm: SM) -> None:
    server = oidc_server()
    verifier, challenge = pkce()
    async with httpx.AsyncClient(base_url=server.url) as c:
        code = (await android_code(c, "alice", challenge))["code"][0]
        async with sm() as db:
            await db.execute(
                update(DeviceCode)
                .where(DeviceCode.used_at.is_(None))
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await db.commit()
        body = {"code": code, "verifier": verifier, "device_name": "Pixel"}
        assert (await c.post("/v1/auth/device-code", json=body)).status_code == 400


async def test_android_sign_in_needs_a_challenge_and_reports_errors_to_the_app(
    oidc_server: Callable[..., ServerHandle],
) -> None:
    async with httpx.AsyncClient(base_url=oidc_server().url) as c:
        assert (await c.get("/auth/login", params={"client": "android"})).status_code == 400
        bad = {"client": "android", "challenge": "short"}
        assert (await c.get("/auth/login", params=bad)).status_code == 422
        q = await android_code(c, "deny", pkce()[1])
        assert q == {"error": ["access_denied"]}


# --- the owner account -------------------------------------------------------------------------


@pytest.fixture
async def unclaimed_owner(sm: SM) -> AsyncIterator[None]:
    """The owner account as fresh from the migration; restored afterwards (shared DB)."""
    async with sm() as db:
        owner = await db.get(User, OWNER_USER_ID)
        assert owner is not None
        saved = (owner.oidc_sub, owner.email, owner.display_name)
        owner.oidc_sub = None
        await db.commit()
    yield
    async with sm() as db:
        await db.execute(
            update(User)
            .where(User.id == OWNER_USER_ID)
            .values(oidc_sub=saved[0], email=saved[1], display_name=saved[2])
        )
        await db.commit()


async def test_owner_email_claims_the_owner_account(
    oidc_server: Callable[..., ServerHandle], issuer: FakeIssuer, unclaimed_owner: None, sm: SM
) -> None:
    sub = f"owner-{uuid.uuid4().hex[:8]}"
    email = f"{sub}@example.com"
    issuer.users[sub] = {"email": email, "name": "The Owner", "groups": []}
    issuer.users["impostor"] = {"email": email, "name": "x", "groups": [], "email_verified": False}
    server = oidc_server(owner_email=email.upper())
    async with httpx.AsyncClient(base_url=server.url) as c:
        # an unverified email never claims it
        await login(c, "impostor")
        assert (await c.get("/v1/me")).json()["id"] != str(OWNER_USER_ID)
        await login(c, sub)
        me = (await c.get("/v1/me")).json()
        assert (me["id"], me["display_name"], me["is_admin"]) == (
            str(OWNER_USER_ID),
            "The Owner",
            True,
        )
    async with sm() as db:
        impostor = (await db.execute(select(User).where(User.oidc_sub == "impostor"))).scalar_one()
        await db.delete(impostor)
        await db.commit()


async def test_claim_owner_moves_a_users_data_to_the_owner(sm: SM, unclaimed_owner: None) -> None:
    email = f"late-{uuid.uuid4().hex[:8]}@example.com"
    user = await create_user(sm, "Late Owner", email=email, oidc_sub=f"sub-{email}")
    device_id, _ = await new_device(sm, "phone", user_id=user.id)
    sid = await make_session(sm, device_id)
    owner = await claim_owner(sm, email.upper())
    assert (owner.id, owner.email, owner.oidc_sub) == (OWNER_USER_ID, email, f"sub-{email}")
    async with sm() as db:
        assert (await db.get(SessionRow, sid)).user_id == OWNER_USER_ID  # type: ignore[union-attr]
        assert (await db.get(Device, device_id)).user_id == OWNER_USER_ID  # type: ignore[union-attr]
        assert await db.get(User, user.id) is None
    with pytest.raises(LookupError):
        await claim_owner(sm, "nobody@example.com")


# --- migration ---------------------------------------------------------------------------------


async def test_migration_gives_existing_devices_and_sessions_to_the_owner(
    postgres_url: str,
) -> None:
    """Upgrade a 0001 database that already has data."""
    dsn = postgres_url.replace("postgresql+asyncpg://", "postgresql://")
    name = f"mig_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = postgres_url.rsplit("/", 1)[0] + f"/{name}"
    try:
        await asyncio.to_thread(command.upgrade, alembic_config(url), "0001")
        conn = await asyncpg.connect(dsn.rsplit("/", 1)[0] + f"/{name}")
        try:
            dev = uuid.uuid4()
            await conn.execute(
                "INSERT INTO devices (id, name, token_hash) VALUES ($1, 'phone', 'h')", dev
            )
            sid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO sessions (id, device_id, mode) VALUES ($1, $2, 'solo')", sid, dev
            )
            await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
            assert await conn.fetchval("SELECT user_id FROM devices") == OWNER_USER_ID
            assert await conn.fetchval("SELECT user_id FROM sessions") == OWNER_USER_ID
            owner = await conn.fetchrow("SELECT * FROM users")
            assert (owner["oidc_sub"], owner["is_admin"], owner["notes_subdir"]) == (
                None,
                True,
                None,
            )
            await asyncio.to_thread(command.downgrade, alembic_config(url), "0001")
            await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
        finally:
            await conn.close()
    finally:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
