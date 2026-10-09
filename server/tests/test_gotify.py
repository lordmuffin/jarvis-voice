import json
from pathlib import Path

import httpx

from jarvis_live.notify.gotify import GotifyNotifier


def _notifier(tmp_path: Path, handler, *, token: str | None = "tok\n") -> GotifyNotifier:  # type: ignore[no-untyped-def]
    f = tmp_path / "token"
    if token is not None:
        f.write_text(token)
    return GotifyNotifier(
        "http://gotify.local/", f, httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def test_payload_shape(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"id": 1})

    await _notifier(tmp_path, handler).notify("T", "**m**", 5, url="https://j.example/sessions/1")
    (req,) = seen
    assert req.method == "POST" and str(req.url) == "http://gotify.local/message"
    assert req.headers["x-gotify-key"] == "tok"
    assert json.loads(req.content) == {
        "title": "T",
        "message": "**m**",
        "priority": 5,
        "extras": {
            "client::display": {"contentType": "text/markdown"},
            "client::notification": {"click": {"url": "https://j.example/sessions/1"}},
        },
    }


async def test_no_click_extra_without_url(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200)

    await _notifier(tmp_path, handler).notify("T", "m", 8)
    assert "client::notification" not in json.loads(seen[0].content)["extras"]


async def test_server_error_is_swallowed(tmp_path: Path) -> None:
    await _notifier(tmp_path, lambda r: httpx.Response(500)).notify("T", "m", 8)


async def test_transport_error_and_missing_token_file_are_swallowed(tmp_path: Path) -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    await _notifier(tmp_path, boom).notify("T", "m", 8)
    await _notifier(tmp_path, lambda r: httpx.Response(200), token=None).notify("T", "m", 8)


async def test_unconfigured_is_a_noop() -> None:
    called = False

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    c = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await GotifyNotifier("", None, c).notify("T", "m", 5)
    await GotifyNotifier("http://x", None, c).notify("T", "m", 5)
    assert not called
