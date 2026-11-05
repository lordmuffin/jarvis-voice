"""What the server sends the model for one spoken request, and what it reads back.

R2 (docs/plans/fast-tier.md) found three faults in the request that every model hit: Today had no
weekday, the grammar let a proposal leave out what its kind needs, and the repo list's order chose
the repo. These pin the fixes. The copilot shares ``ProposalIn`` and ``Proposals`` for parsing;
only the voice request's grammar changes, so the copilot's request is pinned unchanged."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from jarvis_live.actions.schema import ProposalIn
from jarvis_live.actions.state import Context, build_payload
from jarvis_live.copilot.schema import CopilotDelta, FinalNote
from jarvis_live.voice.schema import SPOKEN_KINDS, VoiceRequest
from jarvis_live.voice.service import VOICE_SYSTEM, named_today

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)
REPO = "lordmuffin/jarvis-voice"
CTX = Context(NOW, "UTC", lambda _: False, frozenset({REPO}))
VALID: dict[str, dict[str, Any]] = {
    "task.create": {"text": "Review the pricing proposal", "due": "2026-11-06"},
    "reminder.schedule": {
        "title": "Send the summary",
        "at": (NOW + timedelta(days=5)).isoformat(),
        "body": "To the team",
    },
    "git.pr_comment": {"repo": REPO, "pr": 214, "body": "Needs a rollback note"},
}


def _kinds() -> dict[str, dict[str, Any]]:
    items = VoiceRequest.model_json_schema()["properties"]["proposals"]["items"]
    return {k["properties"]["kind"]["const"]: k for k in items["anyOf"]}


def test_today_names_its_weekday() -> None:
    assert named_today(date(2026, 11, 4)) == "Wednesday 2026-11-04"
    assert named_today(date(2026, 11, 7)) == "Saturday 2026-11-07"


def test_a_spoken_time_without_an_offset_is_read_in_the_owners_timezone() -> None:
    """A bare ISO time such as "at ten" is ten on the owner's clock, not UTC's."""
    ctx = Context(NOW, "America/Chicago", lambda _: False, frozenset())
    p = ProposalIn(kind="reminder.schedule", title="Call the vendor", at="2026-11-06T10:00:00")
    payload = build_payload(p, ctx)
    assert payload is not None
    assert payload["at"] == "2026-11-06T16:00:00+00:00"


def test_proposals_are_validated_in_the_configured_timezone() -> None:
    from jarvis_live.app import _action_context
    from jarvis_live.config import Settings

    ctx = _action_context(Settings(timezone="America/Chicago"), frozenset())
    assert ctx.timezone == "America/Chicago"


def test_the_grammar_offers_exactly_the_kinds_the_prompt_names() -> None:
    assert set(_kinds()) == set(SPOKEN_KINDS) == set(VALID)
    for kind in VALID:
        assert f"{kind} {{" in VOICE_SYSTEM


@pytest.mark.parametrize("kind", list(VALID))
def test_each_kind_requires_what_the_server_needs_to_file_it(kind: str) -> None:
    schema = _kinds()[kind]
    required = [f for f in schema["required"] if f != "kind"]
    assert set(VALID[kind]) <= set(schema["properties"]) - {"kind"}
    assert build_payload(ProposalIn(kind=kind, **VALID[kind]), CTX) is not None  # type: ignore[arg-type]
    for name in required:
        less = {k: v for k, v in VALID[kind].items() if k != name}
        assert build_payload(ProposalIn(kind=kind, **less), CTX) is None, name  # type: ignore[arg-type]
    for name in set(VALID[kind]) - set(required):
        less = {k: v for k, v in VALID[kind].items() if k != name}
        assert build_payload(ProposalIn(kind=kind, **less), CTX) is not None, name  # type: ignore[arg-type]


def test_a_pr_comment_without_a_body_and_a_reminder_without_a_title_are_not_offered() -> None:
    kinds = _kinds()
    assert "body" in kinds["git.pr_comment"]["required"]
    assert "title" in kinds["reminder.schedule"]["required"]


def test_the_voice_request_still_parses_as_tolerantly_as_before() -> None:
    assert VoiceRequest.model_validate({}).target == "none"
    reply = VoiceRequest.model_validate(
        {
            "target": "action",
            "proposals": [
                {"kind": "task.create", "text": "x", "extra": 1},
                {"kind": "pi.session", "title": "x", "text": "y"},
                {"kind": "nonsense"},
            ],
        }
    )
    assert [p.kind for p in reply.proposals] == ["task.create"]


def test_the_copilots_requests_are_unchanged() -> None:
    """Snapshot taken at fc4b075, before the voice grammar split from the shared shape."""
    pinned = json.loads((FIXTURES / "copilot_request_schemas.json").read_text())
    assert pinned == {
        "CopilotDelta": CopilotDelta.model_json_schema(),
        "FinalNote": FinalNote.model_json_schema(),
    }


def test_the_repo_rule_never_picks_a_repo_for_its_place_in_the_list() -> None:
    assert (
        "repo: for cloud, exactly one of the repositories listed, only if the request names it "
        "or says what it is for. Otherwise null: never pick one for its place in the list."
    ) in VOICE_SYSTEM
    assert "null if it is not clear which" not in VOICE_SYSTEM
