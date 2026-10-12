"""Rendering work items into Claude Code prompts from the checked-in template."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jarvis_live.config import Settings
from jarvis_live.workitems.template import (
    DEFAULT_DONE_WHEN,
    TEMPLATE_RELPATH,
    PromptInput,
    PromptTemplate,
    TemplateError,
    branch_name,
    find_template,
    render_prompt,
)

GOLDEN = Path(__file__).parent / "golden" / "work_item_prompt.md"
REPO_TEMPLATE = Path(__file__).resolve().parents[2] / TEMPLATE_RELPATH
CFG = Settings(public_base_url="https://live.example", timezone="America/Chicago")
SID = "3f2a9c1e-0000-4000-8000-000000000001"

ITEM = PromptInput(
    session_id=SID,
    session_title="Friday brain dump",
    started_at=datetime(2026, 10, 9, 23, 30, tzinfo=UTC),  # still the 9th in Chicago
    ref="w1",
    title="Retry failed Gotify notifications",
    repo="lordmuffin/jarvis-voice",
    intent="Gotify pushes vanish when Gotify restarts. Retry a failed push with backoff.",
    acceptance_criteria=[
        "A failed push is retried with backoff, at most three attempts",
        "Giving up is logged",
    ],
    done_when="A test shows a push delivered after Gotify is down for the first two tries.",
    sources=[
        {
            "segment_id": "a",
            "start_ms": 21_000,
            "end_ms": 33_000,
            "text": "The Gotify pushes vanish.",
        },
        {"segment_id": "b", "start_ms": 1_263_000, "end_ms": 1_279_000, "text": "Cap it at three."},
    ],
)


def template() -> PromptTemplate:
    return PromptTemplate.load(REPO_TEMPLATE)


def test_golden_prompt() -> None:
    out = render_prompt(template(), ITEM, CFG)
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.write_text(out)
    assert out == GOLDEN.read_text()


def test_prompt_has_plan_first_tests_and_done_when() -> None:
    out = render_prompt(template(), ITEM, CFG)
    assert "## First: read the repo and plan" in out
    assert "Before changing anything, write a short plan" in out
    assert "## Tests" in out
    assert out.rstrip().endswith(ITEM.done_when or "")
    assert "## Done when" in out
    assert f"`{branch_name(SID, 'w1', ITEM.title)}`" in out


def test_source_links_jump_to_the_moment() -> None:
    out = render_prompt(template(), ITEM, CFG)
    assert f"- [21:03](https://live.example/sessions/{SID}#t=1263000): “Cap it at three.”" in out
    plain = render_prompt(template(), ITEM, Settings(public_base_url=""))
    assert "- 21:03: “Cap it at three.”" in plain
    assert "(no link configured)" in plain


def test_values_are_never_expanded_again() -> None:
    sneaky = PromptInput(**{**ITEM.__dict__, "intent": "Say {{repo}} and {{ title }} literally."})
    out = render_prompt(template(), sneaky, CFG)
    assert "Say {{repo}} and {{ title }} literally." in out


def test_defaults_for_missing_fields() -> None:
    bare = PromptInput(
        **{**ITEM.__dict__, "repo": None, "acceptance_criteria": [], "done_when": None}
    )
    out = render_prompt(template(), bare, CFG)
    assert "(no repository chosen yet)" in out
    assert "(none yet: add at least one before approving)" in out
    assert out.rstrip().endswith(DEFAULT_DONE_WHEN)


def test_unknown_or_missing_placeholders_fail_at_load() -> None:
    with pytest.raises(TemplateError, match="unknown placeholder"):
        PromptTemplate(REPO_TEMPLATE.read_text() + "\n{{secret}}\n")
    with pytest.raises(TemplateError, match="missing placeholder.*done_when"):
        PromptTemplate(REPO_TEMPLATE.read_text().replace("{{done_when}}", ""))
    with pytest.raises(TemplateError, match="cannot read"):
        PromptTemplate.load(Path("/nonexistent/work-item.md"))


def test_the_repo_template_is_found(tmp_path: Path) -> None:
    assert find_template(Settings()) == REPO_TEMPLATE
    own = tmp_path / "t.md"
    assert find_template(Settings(work_template=own)) == own
