"""Two users cannot see or change each other's data.

Every route that takes a session or work item id is driven as a second, ordinary user against
the first user's session and must answer 404 (as if it did not exist) or 403 (an admin-only
route), never an empty 200. ``test_every_id_route_is_in_the_matrix`` fails when a route is
added without a row here, so coverage cannot rot silently.
"""

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import websockets
from fastapi.routing import APIRoute

from jarvis_live.app import create_app
from jarvis_live.config import Settings
from jarvis_live.db.models import OWNER_USER_ID
from jarvis_live.users import create_user
from tests.conftest import ServerHandle
from tests.fake_kube import FakeKube, FakeTokens
from tests.fakes import add_segments, make_session, new_device
from tests.work_item_helpers import SETTINGS, SM, seed_item, web_client

DISPATCH = {**SETTINGS, "dispatch_enabled": True, "git_sha": "abc1234", "dispatch_poll_s": 0.05}

# (method, path, json body). ``{sid}`` and ``{item}`` are A's session and work item.
SESSION_ROUTES: list[tuple[str, str, Any]] = [
    ("GET", "/v1/sessions/{sid}", None),
    ("GET", "/v1/sessions/{sid}/processing", None),
    ("GET", "/v1/sessions/{sid}/audio/mic.wav", None),
    ("GET", "/v1/sessions/{sid}/audio/system.wav", None),
    ("POST", "/v1/sessions/{sid}/ticket", {"role": "viewer"}),
    ("POST", "/v1/sessions/{sid}/ticket", {"role": "producer"}),
    ("POST", "/v1/sessions/{sid}/end", None),
    ("PATCH", "/v1/sessions/{sid}", {"title": "taken"}),
    ("PUT", "/v1/sessions/{sid}/note", {"markdown": "# taken"}),
    ("POST", "/v1/sessions/{sid}/regenerate", None),
    ("POST", "/v1/sessions/{sid}/process", None),
    ("GET", "/v1/sessions/{sid}/publication", None),
    ("POST", "/v1/sessions/{sid}/publication", None),
    ("POST", "/v1/sessions/{sid}/publication/merge", None),
    ("POST", "/v1/sessions/{sid}/publication/close", None),
    ("POST", "/v1/sessions/{sid}/publication/skip", None),
    ("DELETE", "/v1/sessions/{sid}/publication/skip", None),
    ("GET", "/v1/sessions/{sid}/work-items", None),
]
ITEM_ROUTES: list[tuple[str, str, Any]] = [
    ("GET", "/v1/work-items/{item}", None),
    ("PATCH", "/v1/work-items/{item}", {"title": "taken"}),
    ("POST", "/v1/work-items/{item}/approve", None),
    ("POST", "/v1/work-items/{item}/unapprove", None),
    ("POST", "/v1/work-items/{item}/discard", None),
    ("POST", "/v1/work-items/{item}/restore", None),
    ("POST", "/v1/work-items/{item}/done", None),
    ("POST", "/v1/work-items/{item}/reopen", None),
    ("POST", "/v1/work-items/merge", {"into": "{item}", "sources": ["{sid}"]}),
    ("GET", "/v1/work-items/{item}/prompt", None),
    ("POST", "/v1/work-items/{item}/copied", None),
    ("GET", "/v1/work-items/{item}/events", None),
    ("POST", "/v1/work-items/{item}/dispatch", None),
    ("POST", "/v1/work-items/{item}/dispatch/continue", {"guidance": "go on"}),
    ("POST", "/v1/work-items/{item}/dispatch/cancel", None),
]
# Routes with an id in the path that are not session/work-item data, with the reason.
EXEMPT = {
    "/v1/sessions/{session_id}/stream": "WebSocket: guarded by tickets, tested below",
    "/v1/devices/{device_id}": "covered by test_accounts",
    # Gotify only ever reaches the owner: pinned in test_finalizer (not an HTTP route).
}


@dataclass
class World:
    server: ServerHandle
    sm: SM
    sid: uuid.UUID
    item: uuid.UUID
    a: uuid.UUID
    b: uuid.UUID


@pytest.fixture
async def world(make_server: Callable[..., ServerHandle], sm: SM) -> World:
    """User A with a finished session (audio, transcript, work item); user B with nothing."""
    server = make_server(kube=FakeKube(), app_tokens=FakeTokens(), **DISPATCH)
    a = (await create_user(sm, "Alice")).id
    b = (await create_user(sm, "Bob")).id
    device, _ = await new_device(sm, "alice-phone", user_id=a)
    sid = await make_session(sm, device, status="done")
    await add_segments(sm, sid, [(0, 900, "me", "alice's secret plan")])
    pcm = server.settings.data_dir / "sessions" / str(sid)
    pcm.mkdir(parents=True)
    (pcm / "mic.pcm").write_bytes(b"\x01\x02" * 1600)
    item = await seed_item(sm, sid, a)
    return World(server, sm, sid, item, a, b)


def fill(world: World, value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("{sid}", str(world.sid)).replace("{item}", str(world.item))
    if isinstance(value, list):
        return [fill(world, v) for v in value]
    if isinstance(value, dict):
        return {k: fill(world, v) for k, v in value.items()}
    return value


async def call(c: httpx.AsyncClient, world: World, method: str, path: str, body: Any) -> Any:
    return await c.request(method, fill(world, path), json=fill(world, body))


@pytest.mark.parametrize(("method", "path", "body"), SESSION_ROUTES + ITEM_ROUTES)
async def test_another_user_gets_404_and_learns_nothing(
    world: World, method: str, path: str, body: Any
) -> None:
    async with await web_client(world.server, world.sm, world.b) as c:
        r = await call(c, world, method, path, body)
    assert r.status_code == 404, f"{method} {path} -> {r.status_code} {r.text[:200]}"
    assert "alice" not in r.text.lower() and "secret" not in r.text


async def test_the_same_holds_for_a_device_token(world: World) -> None:
    _, token = await new_device(world.sm, "bob-phone", user_id=world.b)
    async with httpx.AsyncClient(
        base_url=world.server.url, headers={"Authorization": f"Bearer {token}"}
    ) as c:
        for method, path, body in SESSION_ROUTES:
            r = await call(c, world, method, path, body)
            assert r.status_code == 404, f"{method} {path} -> {r.status_code}"


async def test_the_owner_still_gets_through_the_same_routes(world: World) -> None:
    async with await web_client(world.server, world.sm, world.a) as c:
        for path in ("/v1/sessions/{sid}", "/v1/sessions/{sid}/processing"):
            assert (await call(c, world, "GET", path, None)).status_code == 200
        assert (await call(c, world, "GET", "/v1/work-items/{item}", None)).status_code == 200
        assert (
            await call(c, world, "GET", "/v1/sessions/{sid}/audio/mic.wav", None)
        ).status_code == 200


async def test_lists_never_contain_the_other_users_rows(world: World) -> None:
    async with await web_client(world.server, world.sm, world.b) as c:
        page = (await c.get("/v1/sessions")).json()
        assert page["total"] == 0 and page["items"] == []
        assert (await c.get("/v1/work-items")).json()["items"] == []
        assert (await c.get("/v1/sessions", params={"all": "true"})).status_code == 403
        assert (await c.get("/v1/sessions", params={"user_id": str(world.a)})).status_code == 403
        assert (await c.get("/v1/admin/users")).status_code == 403
        assert (await c.get("/v1/publishing")).status_code in (403, 503)  # not a publisher
    async with await web_client(world.server, world.sm, world.a) as c:
        assert (await c.get("/v1/sessions")).json()["total"] == 1


async def test_a_ticket_for_another_session_does_not_open_the_stream(world: World) -> None:
    device, _ = await new_device(world.sm, "bob-mac", user_id=world.b)
    mine = await make_session(world.sm, device)
    async with await web_client(world.server, world.sm, world.b) as c:
        ticket = (await c.post(f"/v1/sessions/{mine}/ticket", json={"role": "viewer"})).json()[
            "ticket"
        ]
    ws_url = world.server.url.replace("http", "ws", 1) + f"/v1/sessions/{world.sid}/stream"
    with pytest.raises(
        (websockets.exceptions.InvalidStatus, websockets.exceptions.ConnectionClosed)
    ):
        async with websockets.connect(f"{ws_url}?ticket={ticket}") as ws:
            await ws.recv()


async def test_admins_read_everything_but_write_only_their_own(world: World) -> None:
    """The documented admin power is to read (and rename); the note, the LLM jobs and the
    publication of someone else's session stay with its owner."""
    admin = (await create_user(world.sm, "Root", is_admin=True)).id
    async with await web_client(world.server, world.sm, admin) as c:
        for path in (
            "/v1/sessions/{sid}",
            "/v1/sessions/{sid}/processing",
            "/v1/sessions/{sid}/audio/mic.wav",
            "/v1/sessions/{sid}/publication",
        ):
            assert (await call(c, world, "GET", path, None)).status_code == 200, path
        assert (
            await call(c, world, "PATCH", "/v1/sessions/{sid}", {"title": "renamed"})
        ).status_code == 200
        for method, path, body in (
            ("PUT", "/v1/sessions/{sid}/note", {"markdown": "# taken"}),
            ("POST", "/v1/sessions/{sid}/regenerate", None),
            ("POST", "/v1/sessions/{sid}/process", None),
            ("POST", "/v1/sessions/{sid}/publication", None),
            ("POST", "/v1/sessions/{sid}/publication/skip", None),
            ("DELETE", "/v1/sessions/{sid}/publication/skip", None),
        ):
            r = await call(c, world, method, path, body)
            assert r.status_code == 403, f"{method} {path} -> {r.status_code}"


async def test_the_homelab_assistant_is_for_the_owner_and_admins(world: World) -> None:
    body = {"question": "is the nas up?", "requestId": "t-1"}
    async with await web_client(world.server, world.sm, world.b) as c:
        assert (await c.post("/v1/homelab/ask", json=body)).status_code == 403
    async with await web_client(world.server, world.sm, world.a) as c:
        assert (await c.post("/v1/homelab/ask", json=body)).status_code == 403
    admin = (await create_user(world.sm, "Root2", is_admin=True)).id
    async with await web_client(world.server, world.sm, admin) as c:
        assert (await c.post("/v1/homelab/ask", json=body)).status_code == 200
    async with await web_client(world.server, world.sm, OWNER_USER_ID) as c:
        assert (await c.post("/v1/homelab/ask", json=body)).status_code == 200


def test_every_id_route_is_in_the_matrix() -> None:
    app = create_app(Settings(**SETTINGS))
    covered = {
        re.sub(r"\{(sid|item)\}", lambda m: "{session_id}" if m[1] == "sid" else "{item_id}", p)
        for _, p, _ in SESSION_ROUTES + ITEM_ROUTES
    }
    missing = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not isinstance(route, APIRoute) and not path.startswith("/v1/sessions/{"):
            continue
        if "{session_id}" not in path and "{item_id}" not in path:
            continue
        norm = re.sub(r"\{channel\}", "mic", path)
        norm = path if norm == path else norm
        if path in EXEMPT:
            continue
        if not any(c == path or c == norm for c in covered):
            missing.append(path)
    assert not missing, f"routes with an id and no isolation test: {sorted(set(missing))}"
