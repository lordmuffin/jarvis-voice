import json
from pathlib import Path

import httpx
import pytest

from jarvis_live.homelab.client import DpiClient, DpiError


def _client(tmp_path: Path, handler, *, token: str | None = "tok\n", **kwargs) -> DpiClient:  # type: ignore[no-untyped-def]
    f = tmp_path / "token"
    if token is not None:
        f.write_text(token)
    return DpiClient(
        "http://dpi-backend.pi-durable.svc.cluster.local:4780/",
        f,
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


async def test_request_shape(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"status": "done", "answer": {"text": "all green"}})

    answer = await _client(tmp_path, handler).ask("is k3s2 healthy?", "conv-1-3")
    assert answer == "all green"
    (req,) = seen
    assert req.method == "POST"
    assert (
        str(req.url)
        == "http://dpi-backend.pi-durable.svc.cluster.local:4780/v1/sessions/jarvis/agents/main/messages"
    )
    assert req.headers["authorization"] == "Bearer tok"
    assert json.loads(req.content) == {
        "content": "is k3s2 healthy?",
        "requestId": "conv-1-3",
        "waitMs": 60_000,
    }


async def test_custom_session_and_wait_ms(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"status": "done", "answer": {"text": "ok"}})

    await _client(tmp_path, handler, session="other", wait_ms=5_000).ask("q", "r-1")
    assert str(seen[0].url).endswith("/v1/sessions/other/agents/main/messages")
    assert json.loads(seen[0].content)["waitMs"] == 5_000


async def test_idempotent_retry_reuses_the_same_request_id(tmp_path: Path) -> None:
    """A transport-level failure (the only safe case to retry) resends with the identical
    requestId, so pi-durable's idempotency admits the eventual delivery exactly once."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if len(seen) == 1:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"status": "done", "answer": {"text": "recovered"}})

    answer = await _client(tmp_path, handler).ask("ping", "fixed-id-7")
    assert answer == "recovered"
    assert len(seen) == 2
    ids = {json.loads(seen[0].content)["requestId"], json.loads(seen[1].content)["requestId"]}
    assert ids == {"fixed-id-7"}


async def test_gives_up_after_one_retry(tmp_path: Path) -> None:
    def always_down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(DpiError):
        await _client(tmp_path, always_down).ask("ping", "r-1")


async def test_http_error_is_not_retried(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(401, json={"error": {"code": "unauthorized", "message": "nope"}})

    with pytest.raises(DpiError):
        await _client(tmp_path, handler).ask("ping", "r-1")
    assert len(seen) == 1


async def test_unanswered_and_still_pending_raise(tmp_path: Path) -> None:
    with pytest.raises(DpiError):
        await _client(
            tmp_path, lambda r: httpx.Response(200, json={"status": "unanswered", "reason": "busy"})
        ).ask("ping", "r-1")

    def pending(r: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"submissionId": "s1", "status": "pending"})

    with pytest.raises(DpiError):
        await _client(tmp_path, pending).ask("ping", "r-2")


async def test_configured_but_token_file_missing_raises_cleanly(tmp_path: Path) -> None:
    """JARVIS_LIVE_DPI_TOKEN_FILE is a fixed path set as soon as dpi_base_url is, so the file
    not existing yet (the 1Password field hasn't landed) must not be an unhandled OSError."""
    called = False

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"status": "done", "answer": {"text": "x"}})

    c = DpiClient(
        "http://dpi-backend.pi-durable.svc.cluster.local:4780",
        tmp_path / "missing",  # never written
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert c.configured
    with pytest.raises(DpiError):
        await c.ask("ping", "r-1")
    assert not called


async def test_unconfigured_raises_without_a_request(tmp_path: Path) -> None:
    called = False

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    c = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(DpiError):
        await DpiClient("", None, c).ask("ping", "r-1")
    with pytest.raises(DpiError):
        await DpiClient("http://x", None, c).ask("ping", "r-1")
    assert not called
