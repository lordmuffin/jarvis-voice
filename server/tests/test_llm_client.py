import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from jarvis_live.copilot.schema import CopilotDelta
from jarvis_live.llm.client import LiteLLMClient, LLMError


def _completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _client(handler: Any, **kw: Any) -> LiteLLMClient:
    return LiteLLMClient(
        "http://litellm/v1/", httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kw
    )


async def _call(c: LiteLLMClient) -> CopilotDelta:
    return await c.complete_json(
        model="fast", system="/no_think sys", user="u", schema=CopilotDelta
    )


async def test_request_shape_and_parse(tmp_path: Path) -> None:
    key = tmp_path / "key"
    key.write_text("sk-secret\n")
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return _completion('{"topics": ["a"]}')

    delta = await _call(_client(handler, api_key_file=key))
    assert delta.topics == ["a"]
    (req,) = seen
    assert str(req.url) == "http://litellm/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-secret"
    body = json.loads(req.content)
    assert body["model"] == "fast"
    assert body["messages"][0] == {"role": "system", "content": "/no_think sys"}
    rf = body["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "CopilotDelta"
    assert "notes_upsert" in rf["json_schema"]["schema"]["properties"]


async def test_no_auth_header_without_key_file() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return _completion("{}")

    await _call(_client(handler))
    assert "authorization" not in seen[0].headers


async def test_retries_once_on_invalid_json() -> None:
    replies = iter(["not json at all", '{"topics": ["ok"]}'])
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _completion(next(replies))

    assert (await _call(_client(handler))).topics == ["ok"]
    assert calls == 2


async def test_second_invalid_json_raises() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _completion('{"suggestions": [{"kind": "bogus", "text": "x"}]}')

    with pytest.raises(LLMError):
        await _call(_client(handler))
    assert calls == 2


async def test_http_error_raises_without_retry() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    with pytest.raises(LLMError):
        await _call(_client(handler))
    assert calls == 1


async def test_strips_think_block_and_code_fence() -> None:
    content = '<think>hmm</think>\n```json\n{"topics": ["x"]}\n```'
    assert (await _call(_client(lambda r: _completion(content)))).topics == ["x"]


def test_delta_trims_topics_and_ignores_unknown_keys() -> None:
    d = CopilotDelta.model_validate({"topics": [" a ", "", "b", "c", "d", "e", "f"], "extra": 1})
    assert d.topics == ["a", "b", "c", "d", "e"]
