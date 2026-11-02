"""Eval-only reply variants and the bar for step 4, against canned output (no --live needed)."""

import json
import re
from typing import Any

import httpx
import pytest

from jarvis_live.config import Settings
from jarvis_live.copilot.prompts import COPILOT_SYSTEM
from jarvis_live.copilot.schema import CopilotDelta
from jarvis_live.llm.client import LiteLLMClient
from tests.evals.harness import run_case
from tests.evals.reply_size import (
    BAR_CASES,
    Recorder,
    Row,
    can_still_pass,
    reply_p95,
    verdict,
)
from tests.evals.transcripts import CASES, ROLLING_REPLAY, SMALL_MODEL_CASES, VENDOR_CALL
from tests.evals.variants import TODAY, VARIANTS, Variant, compact_grammar, narrowed

ALL_CASES = [*CASES, *SMALL_MODEL_CASES, *ROLLING_REPLAY]


def sent_schema(variant: Variant) -> dict[str, Any]:
    return variant.schema.model_json_schema()


async def body_for(variant: Variant | None) -> dict[str, Any]:
    sent: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        llm = LiteLLMClient("http://gateway/v1", http)
        args = {} if variant is None else {"variant": variant}
        await run_case(llm, VENDOR_CALL, **args)
    return sent[0]


async def test_the_default_is_todays_request_exactly() -> None:
    assert TODAY.system is COPILOT_SYSTEM
    assert TODAY.schema is CopilotDelta
    assert await body_for(None) == await body_for(TODAY)


@pytest.mark.parametrize("variant", VARIANTS.values(), ids=lambda v: v.name)
async def test_a_variant_reaches_the_request(variant: Variant) -> None:
    body = await body_for(variant)
    assert body["messages"][0]["content"] == variant.system
    assert body["response_format"]["json_schema"]["name"] == "CopilotDelta"
    assert body["response_format"]["json_schema"]["schema"] == sent_schema(variant)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize("variant", VARIANTS.values(), ids=lambda v: v.name)
def test_no_variant_caps_actions_or_stops_asking_for_owners(variant: Variant) -> None:
    schema = sent_schema(variant)
    assert "maxItems" not in schema["properties"]["actions_upsert"]
    assert "actions_upsert" in schema["required"]
    assert {"text", "owner", "due"} <= set(schema["$defs"]["ActionUpsert"]["properties"])
    assert "- notes_upsert / decisions_upsert / actions_upsert:" in variant.system
    assert "Do not invent owners or due dates" in variant.system


def test_narrowing_caps_a_list_in_the_schema() -> None:
    schema = narrowed(caps={"notes_upsert": 2, "topics": 3}).model_json_schema()
    assert schema["properties"]["notes_upsert"]["maxItems"] == 2
    assert schema["properties"]["topics"]["maxItems"] == 3


def test_narrowing_drops_a_key_from_the_schema_and_keeps_every_other_required() -> None:
    schema = narrowed(drop=("suggestions", "proposals")).model_json_schema()
    assert "suggestions" not in schema["properties"]
    assert "proposals" not in schema["properties"]
    assert schema["required"] == [
        k for k in CopilotDelta.model_fields if k not in ("suggestions", "proposals")
    ]


def test_narrowing_can_require_a_nested_action_key() -> None:
    schema = narrowed(action_required=("text", "owner", "due")).model_json_schema()
    assert schema["$defs"]["ActionUpsert"]["required"] == ["text", "owner", "due"]
    assert CopilotDelta.model_json_schema()["$defs"]["ActionUpsert"]["required"] == ["text"]


def test_narrowing_can_ask_for_the_owner_before_the_text() -> None:
    schema = narrowed(action_required=("owner", "due", "text")).model_json_schema()
    action = schema["$defs"]["ActionUpsert"]
    assert list(action["properties"])[:3] == ["owner", "due", "text"]
    assert list(CopilotDelta.model_json_schema()["$defs"]["ActionUpsert"]["properties"])[0] == "id"


def test_the_owner_first_variant_keeps_the_rule_against_inventing_owners() -> None:
    v = VARIANTS["slim-owner"]
    assert "Do not invent owners or due dates" in v.system
    assert "owner: the name of the person who will do it" in v.system
    assert list(sent_schema(v)["$defs"]["ActionUpsert"]["properties"])[0] == "owner"


def test_a_narrowed_schema_parses_as_tolerantly_as_todays() -> None:
    parsed = narrowed(drop=("suggestions",), caps={"topics": 1}).model_validate(
        {"actions_upsert": [{"text": "x"}], "topics": ["a", "b"], "suggestions": []}
    )
    assert parsed.actions_upsert[0].owner is None
    assert isinstance(parsed, CopilotDelta)


def test_the_slow_cycle_prompt_loses_those_lines_and_nothing_else() -> None:
    v = VARIANTS["no-sugg-prop"]
    assert "- suggestions:" not in v.system and "- proposals:" not in v.system
    assert "proposals_already_made" not in v.system
    for line in ("- notes_upsert", "- remove_ids:", "- topics:", "- title:", "Rules:"):
        assert line in v.system


def test_the_bar_is_the_eleven_cases_flash_passes_every_time() -> None:
    names = {c.name for c in ALL_CASES}
    assert len(ALL_CASES) == 17
    assert len(BAR_CASES) == 11 and BAR_CASES <= names
    assert not {"long meeting, first 5 minutes", "long meeting, 8 minutes"} & BAR_CASES


def row(case: str, run: int, ok: bool, tokens: tuple[int, ...] = (100,)) -> Row:
    return Row(variant="v", run=run, case=case, ok=ok, fails=[], tokens=list(tokens))


def passing(runs: int = 3, tokens: int = 100) -> list[Row]:
    return [row(c, r, True, (tokens,)) for c in sorted(BAR_CASES) for r in range(runs)]


def test_two_of_three_on_every_bar_case_clears_quality() -> None:
    rows = passing()
    rows[0] = row(rows[0].case, 0, False)
    result = verdict(rows, budget=225)
    assert result.quality_ok and result.size_ok and result.passed
    assert result.bar_cases_met == 11


def test_one_of_three_on_a_bar_case_fails_quality() -> None:
    rows = passing()
    first = rows[0].case
    rows[:2] = [row(first, 0, False), row(first, 1, False)]
    result = verdict(rows, budget=225)
    assert not result.quality_ok and not result.passed
    assert result.bar_cases_met == 10


def test_fewer_than_three_runs_cannot_clear_the_bar() -> None:
    assert not verdict(passing(runs=2), budget=225).passed


def test_a_p95_over_budget_fails_size_even_with_perfect_quality() -> None:
    result = verdict(passing(tokens=226), budget=225)
    assert result.quality_ok and not result.size_ok and not result.passed


def test_a_retry_counts_as_a_reply() -> None:
    rows = [row("a", 0, True, (100, 900))] + [row("a", 0, True, (100,)) for _ in range(18)]
    assert reply_p95(rows) == 900  # the 20th of 20 replies, seen only because the retry counts


def test_a_candidate_stops_once_a_bar_case_can_no_longer_reach_two_of_three() -> None:
    alive = passing(runs=1)
    assert can_still_pass(alive)
    alive[0] = row(alive[0].case, 0, False)
    assert can_still_pass(alive)  # 0 of 1, and two runs left
    assert not can_still_pass(alive + [row(alive[0].case, 1, False)])


async def test_the_recorder_keeps_each_attempt_and_passes_the_reply_through() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "usage": {"completion_tokens": 7, "prompt_tokens": 90},
                "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
            },
        )

    rec = Recorder(httpx.MockTransport(answer))
    async with httpx.AsyncClient(transport=rec) as http:
        llm = LiteLLMClient("http://local/v1", http)
        await run_case(llm, VENDOR_CALL, Settings(copilot_model="m"))
    assert [(a["tokens"], a["prompt"], a["finish"], a["content"]) for a in rec.attempts] == [
        (7, 90, "stop", "{}")
    ]


def test_a_compact_grammar_has_no_whitespace_to_spend_tokens_on() -> None:
    grammar = compact_grammar(VARIANTS["no-sugg-prop-caps"].schema.model_json_schema())
    assert "space" not in grammar
    literals = re.findall(r'"(?:[^"\\]|\\.)*"', grammar.split("\n", 1)[1])  # past char ::=
    assert literals and not [x for x in literals if " " in x or "\\n" in x or "\\t" in x]


def test_a_compact_grammar_keeps_the_keys_in_order_and_requires_them() -> None:
    grammar = compact_grammar(VARIANTS["no-sugg-prop-caps"].schema.model_json_schema())
    root = next(x for x in grammar.splitlines() if x.startswith("root ::="))
    positions = [root.index(f'\\"{k}\\":') for k in ("notes_upsert", "actions_upsert", "title")]
    assert positions == sorted(positions)
    assert "suggestions" not in grammar and "proposals" not in grammar


def test_a_compact_grammar_keeps_the_caps_and_leaves_actions_open() -> None:
    grammar = compact_grammar(VARIANTS["no-sugg-prop-caps"].schema.model_json_schema())
    assert '( "," note-upsert ){0,1}' in grammar  # 2 at most
    assert '( "," action-upsert )*' in grammar


def test_a_compact_grammar_makes_id_optional_and_text_nonempty() -> None:
    grammar = compact_grammar(VARIANTS["no-sugg-prop-caps"].schema.model_json_schema())
    action = next(x for x in grammar.splitlines() if x.startswith("action-upsert ::="))
    assert '( "," "\\"id\\":"' in action and action.count(")?") == 3
    assert 'string-1 ::= "\\"" char+ "\\""' in grammar


def test_a_compact_grammar_refuses_a_shape_it_cannot_write() -> None:
    with pytest.raises(ValueError, match="integer"):
        compact_grammar({"type": "object", "properties": {"n": {"type": "integer"}}})


async def test_a_grammar_replaces_the_schema_on_the_wire() -> None:
    sent: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    rec = Recorder(httpx.MockTransport(answer), grammar='root ::= "{}"')
    async with httpx.AsyncClient(transport=rec) as http:
        await run_case(LiteLLMClient("http://local/v1", http), VENDOR_CALL)
    assert sent[0]["grammar"] == 'root ::= "{}"'
    assert "response_format" not in sent[0]
    assert sent[0]["chat_template_kwargs"] == {"enable_thinking": False}


async def test_without_a_grammar_the_recorder_leaves_the_request_alone() -> None:
    sent: list[bytes] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    plain = await body_for(None)
    rec = Recorder(httpx.MockTransport(answer))
    async with httpx.AsyncClient(transport=rec) as http:
        await run_case(LiteLLMClient("http://local/v1", http), VENDOR_CALL)
    assert json.loads(sent[0]) == plain
