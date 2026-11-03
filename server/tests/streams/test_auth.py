"""MediaMTX asks this hook about every publish, read and API call (authMethod: http)."""

import secrets
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.db.models import OWNER_USER_ID
from jarvis_live.streams import keys
from jarvis_live.streams.auth import ReaderCredential, StreamAuth, auth_app

SM = async_sessionmaker  # type: ignore[type-arg]
RUN = uuid.uuid4().hex[:6]  # the test database outlives one test: names must not repeat
N = {n: f"{n}-{RUN}" for n in ["obs-desk", "phone", "nowhere", "cam", "gone", "busy", "peek"]}
READER = ReaderCredential(user="jarvis-ingest", password=secrets.token_urlsafe(12))


class Capacity:
    def __init__(self) -> None:
        self.full = False
        self.asked: list[str] = []

    def __call__(self, path: str) -> bool:
        self.asked.append(path)
        return not self.full


@pytest.fixture
def capacity() -> Capacity:
    return Capacity()


@pytest.fixture
async def client(sm: SM, capacity: Capacity) -> AsyncIterator[httpx.AsyncClient]:
    app = auth_app(StreamAuth(sm, READER, capacity))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://auth"
    ) as c:
        yield c


async def _ask(client: httpx.AsyncClient, **fields: str) -> int:
    body = {"user": "", "password": "", "token": "", "ip": "10.0.0.5", "id": "x", "query": ""}
    body |= {"protocol": "srt", "userAgent": "obs"} | fields
    return (await client.post("/mediamtx/auth", json=body)).status_code


async def test_publish_needs_the_paths_own_key(sm: SM, client: httpx.AsyncClient) -> None:
    secret = await keys.create_key(sm, N["obs-desk"], OWNER_USER_ID)
    other = await keys.create_key(sm, N["phone"], OWNER_USER_ID)
    assert (
        await _ask(client, action="publish", path=N["obs-desk"], user="x", password=secret) == 200
    )
    assert await _ask(client, action="publish", path=N["obs-desk"], user="x", password=other) == 401
    assert await _ask(client, action="publish", path=N["obs-desk"], user="x", password="no") == 401
    assert await _ask(client, action="publish", path=N["nowhere"], user="x", password=secret) == 401


async def test_an_rtsp_client_without_credentials_is_challenged(client: httpx.AsyncClient) -> None:
    # RTSP clients send credentials only after a 401: an empty attempt must get one.
    assert await _ask(client, action="publish", path=N["obs-desk"]) == 401


async def test_the_key_may_come_as_a_token_or_in_the_query(
    sm: SM, client: httpx.AsyncClient
) -> None:
    secret = await keys.create_key(sm, N["cam"], OWNER_USER_ID)
    assert await _ask(client, action="publish", path=N["cam"], token=secret) == 200
    query = f"user=obs&pass={secret}"
    assert await _ask(client, action="publish", path=N["cam"], protocol="rtmp", query=query) == 200


async def test_a_revoked_key_cannot_publish(sm: SM, client: httpx.AsyncClient) -> None:
    secret = await keys.create_key(sm, N["gone"], OWNER_USER_ID)
    await keys.revoke_key(sm, N["gone"])
    assert await _ask(client, action="publish", path=N["gone"], user="x", password=secret) == 401


async def test_publish_is_refused_when_ingest_is_full(
    sm: SM, client: httpx.AsyncClient, capacity: Capacity
) -> None:
    secret = await keys.create_key(sm, N["busy"], OWNER_USER_ID)
    capacity.full = True
    assert await _ask(client, action="publish", path=N["busy"], user="x", password=secret) == 401
    assert capacity.asked == [N["busy"]]


async def test_a_publish_key_cannot_read_or_use_the_api(sm: SM, client: httpx.AsyncClient) -> None:
    secret = await keys.create_key(sm, N["peek"], OWNER_USER_ID)
    for action in ("read", "playback", "api", "metrics", "pprof"):
        assert await _ask(client, action=action, path=N["peek"], user="x", password=secret) == 401


async def test_only_the_servers_reader_may_read_and_use_the_api(client: httpx.AsyncClient) -> None:
    ok = {"user": "jarvis-ingest", "password": READER.password}
    assert await _ask(client, action="read", path=N["obs-desk"], protocol="rtsp", **ok) == 200
    assert await _ask(client, action="api", path="", protocol="", **ok) == 200
    assert await _ask(client, action="metrics", path="", **ok) == 401
    bad = {"user": "jarvis-ingest", "password": "guess"}
    assert await _ask(client, action="read", path=N["obs-desk"], **bad) == 401


async def test_a_malformed_request_is_refused(client: httpx.AsyncClient) -> None:
    r = await client.post("/mediamtx/auth", content=b"not json")
    assert r.status_code in (401, 422)
