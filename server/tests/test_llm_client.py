import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from jarvis_live.copilot.schema import CopilotDelta, FinalNote
from jarvis_live.llm.client import LiteLLMClient, LLMError
from jarvis_live.voice.schema import VoiceRequest
from jarvis_live.workitems.schema import IdeasDelta


def _completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _client(handler: Any, **kw: Any) -> LiteLLMClient:
    return LiteLLMClient(
        "http://litellm/v1/", httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kw
    )


COPILOT_KEYS = [
    "notes_upsert",
    "actions_upsert",
    "decisions_upsert",
    "remove_ids",
    "suggestions",
    "proposals",
    "topics",
    "title",
]


async def _sent_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema the real client puts on the wire for this model."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return _completion('{"title": "t", "summary": "s"}')

    await _client(handler).complete_json(model="m", system="s", user="u", schema=schema)
    sent: dict[str, Any] = json.loads(seen[0].content)["response_format"]["json_schema"]["schema"]
    return sent


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


async def test_thinking_is_left_to_the_model_by_default() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return _completion("{}")

    await _call(_client(handler))
    assert "chat_template_kwargs" not in json.loads(seen[0].content)


async def test_think_false_asks_the_chat_template_to_skip_reasoning() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return _completion("{}")

    await _client(handler).complete_json(
        model="heavy", system="s", user="u", schema=CopilotDelta, think=False
    )
    body = json.loads(seen[0].content)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


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


async def test_every_copilot_key_is_required_on_the_wire() -> None:
    # Optional keys let the structured output grammar skip them, and Flash skips actions_upsert.
    sent = await _sent_schema(CopilotDelta)
    assert sent["required"] == COPILOT_KEYS
    assert list(sent["properties"]) == COPILOT_KEYS


async def test_a_required_title_can_still_be_null() -> None:
    title = (await _sent_schema(CopilotDelta))["properties"]["title"]
    assert {"type": "null"} in title["anyOf"]


@pytest.mark.parametrize(
    ("schema", "required", "nested"),
    [
        (
            CopilotDelta,
            COPILOT_KEYS,
            {
                "ActionUpsert": ["text"],
                "Evidence": ["start_ms"],
                "NoteUpsert": ["text"],
                "ProposalIn": ["kind"],
                "SuggestionIn": ["kind", "text"],
            },
        ),
        (
            FinalNote,
            ["title", "summary"],
            {"Evidence": ["start_ms"], "FinalAction": ["text"], "ProposalIn": ["kind"]},
        ),
        (VoiceRequest, None, {"Evidence": ["start_ms"], "ProposalIn": ["kind"]}),
        (IdeasDelta, None, {"ItemUpsert": ["title"]}),
    ],
    ids=lambda v: v.__name__ if isinstance(v, type) else "",
)
async def test_each_callers_required_keys_are_exactly_these(
    schema: type[BaseModel], required: list[str] | None, nested: dict[str, list[str]]
) -> None:
    sent = await _sent_schema(schema)
    assert sent.get("required") == required
    assert {name: d.get("required") for name, d in sent["$defs"].items()} == nested


@pytest.mark.parametrize("missing", COPILOT_KEYS)
async def test_a_reply_missing_one_key_parses_with_todays_default(missing: str) -> None:
    # The gateway's ollama route may not enforce the schema, and a retry costs a whole cycle.
    full = CopilotDelta().model_dump()
    reply = json.dumps({k: v for k, v in full.items() if k != missing})
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _completion(reply)

    assert await _call(_client(handler)) == CopilotDelta()
    assert calls == 1


async def test_an_empty_reply_parses_with_todays_defaults() -> None:
    delta = await _call(_client(lambda r: _completion("{}")))
    assert delta == CopilotDelta(
        notes_upsert=[],
        actions_upsert=[],
        decisions_upsert=[],
        remove_ids=[],
        suggestions=[],
        proposals=[],
        topics=[],
        title=None,
    )


def test_delta_still_ignores_unknown_keys_after_requiring_the_known_ones() -> None:
    assert CopilotDelta.model_validate({"extra": 1}) == CopilotDelta()
