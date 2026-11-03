import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jarvis_live.copilot.schema import FinalAction, FinalNote
from jarvis_live.copilot.transcript import Line
from jarvis_live.finalize.render import (
    NoteMeta,
    NoteProposal,
    NoteWorkItem,
    render_filename,
    render_note,
    slugify,
)

GOLDEN = Path(__file__).parent / "golden" / "final_note.md"

NOTE = FinalNote(
    title="Acme renewal: pricing & SLA",
    summary="Agreed to renew Acme for 12 months.\nPricing stays flat; SLA to be tightened.",
    decisions=["Renew for 12 months", "Keep pricing flat"],
    actions=[
        FinalAction(text="Send revised quote", owner="Dana", due="2026-10-16"),
        FinalAction(text="Review SLA draft", owner="me"),
        FinalAction(text="Book legal review"),
    ],
    open_questions=["Who signs on our side?"],
    follow_ups=["Check Q4 budget"],
    related=["Acme Renewal", "SLA Template"],
)
META = NoteMeta(
    created=datetime(2026, 10, 9, 9, 30, tzinfo=ZoneInfo("America/Chicago")),
    session_id="11111111-2222-3333-4444-555555555555",
    device="Andrew's MacBook",
    mode="meeting",
    duration_minutes=42,
)
LINES = [
    Line(1_000, 4_000, "them", "Thanks for joining."),
    Line(5_000, 9_000, "me", "Happy to.  Let's start with   pricing."),
    Line(3_725_000, 3_730_000, "them", "One last thing."),
]


def test_golden_render() -> None:
    out = render_note(NOTE, META, LINES)
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.write_text(out)
    assert out == GOLDEN.read_text()


def test_sections_in_order_and_empty_sections_omitted() -> None:
    sparse = FinalNote(title="Solo", summary="Thinking out loud.")
    out = render_note(sparse, META, [])
    heads = [line for line in out.splitlines() if line.startswith("## ")]
    assert heads == ["## Summary", "## Transcript"]
    full = [line for line in render_note(NOTE, META, LINES).splitlines() if line.startswith("## ")]
    assert full == [
        "## Summary",
        "## Actions",
        "## Decisions",
        "## Open questions",
        "## Follow-ups",
        "## Related",
        "## Transcript",
    ]


def frontmatter(md: str) -> dict[str, str]:
    head = md.split("---\n", 2)[1]
    return dict(line.split(": ", 1) for line in head.splitlines())


def test_frontmatter_follows_the_vault_ai_output_template() -> None:
    fm = frontmatter(render_note(NOTE, META, LINES))
    assert fm["type"] == "ai-output"
    assert fm["date"] == "2026-10-09"
    assert fm["created"] == "2026-10-09T09:30:00-05:00"
    assert fm["status"] == "draft"
    assert fm["generated-by"] == "jarvis-live"
    assert fm["session_id"] == META.session_id  # the finalizer finds its own file by this line
    assert fm["tags"] == "[ai-output, jarvis-live, inbox]"


def test_default_filename_is_the_vault_inbox_style() -> None:
    from jarvis_live.config import Settings

    template = Settings().final_filename_template
    assert (
        render_filename(template, META.created, NOTE.title, "sid")
        == "2026-10-09 - Acme renewal pricing & SLA.md"
    )


def test_filename_template_and_slug() -> None:
    assert (
        render_filename("{date} {time} - {slug}.md", META.created, NOTE.title, "sid")
        == "2026-10-09 0930 - Acme renewal pricing & SLA.md"
    )
    assert slugify('a/b: "c"?') == "a b c"
    assert slugify("x" * 100) == "x" * 60
    assert slugify("///") == "untitled"
    assert render_filename("{session_id}/{slug}.md", META.created, "T", "sid") == "sid-T.md"


WORK = [
    NoteWorkItem(
        title="Retry failed Gotify notifications",
        repo="lordmuffin/jarvis-voice",
        state="approved",
        intent="Retry a failed push with backoff.",
        criteria=["At most three attempts"],
        sources=[(21_000, "https://live.example/s#t=21000"), (1_263_000, "")],
        review_url="https://live.example/s",
        prompt="You are working in lordmuffin/jarvis-voice.\n\n## Task\nRetry.",
    ),
    NoteWorkItem(
        title="SRT export CLI",
        repo=None,
        state="draft",
        intent="",
        criteria=[],
        sources=[],
        review_url="",
    ),
]


def test_work_items_section() -> None:
    out = render_note(NOTE, META, LINES, WORK)
    heads = [line for line in out.splitlines() if line.startswith("## ")]
    assert heads.index("## Work items") == heads.index("## Follow-ups") + 1
    section = out.split("## Work items\n", 1)[1].split("\n## ", 1)[0]
    assert section == (
        "\n### Retry failed Gotify notifications\n\n"
        "`lordmuffin/jarvis-voice` · approved · [review](https://live.example/s)\n\n"
        "Retry a failed push with backoff.\n\n"
        "- [ ] At most three attempts\n\n"
        "Heard at [00:21](https://live.example/s#t=21000), 21:03\n\n"
        "> [!abstract]- Claude Code prompt\n"
        "> You are working in lordmuffin/jarvis-voice.\n"
        ">\n"
        "> ## Task\n"
        "> Retry.\n\n"
        "### SRT export CLI\n\n"
        "no repository yet · draft\n"
    )
    assert "## Work items" not in render_note(NOTE, META, LINES, [])


def test_source_is_in_the_frontmatter_only_when_the_recording_states_one() -> None:
    import dataclasses

    assert "source" not in frontmatter(render_note(NOTE, META, LINES))
    watch = dataclasses.replace(META, source="watch")
    assert frontmatter(render_note(NOTE, watch, LINES))["source"] == "watch"


def _proposed(out: str) -> list[str]:
    return out.split("## Proposed actions\n\n", 1)[1].split("\n\n", 1)[0].splitlines()


def test_proposed_actions_show_each_kind_as_a_checkbox_snapshot() -> None:
    proposals = [
        NoteProposal(
            "a1", "task.create", {"text": "Send\nthe quote", "due": "2026-10-16"}, "proposed"
        ),
        NoteProposal(
            "a2", "vault.append", {"path": "30 Resources/Sleep.md", "text": "x" * 200}, "succeeded"
        ),
        NoteProposal(
            "a3",
            "reminder.schedule",
            {"title": "Call Dana", "at": "2026-10-09T20:00:00+00:00"},
            "approved",
        ),
        NoteProposal(
            "a4",
            "github.comment",
            {"repo": "o/r", "pr": 7, "body": "LGTM"},
            "failed",
            "403 Forbidden",
        ),
        NoteProposal("a5", "task.create", {"text": "Dropped"}, "rejected"),
        NoteProposal("a6", "task.create", {"text": "Too late"}, "expired"),
    ]
    out = render_note(NOTE, META, LINES, proposals=proposals, review_url="https://live.example/r")
    assert _proposed(out) == [
        "- [ ] Task: Send the quote (due 2026-10-16) <!-- action:a1 -->",
        f"- [x] Append to [[Sleep]]: {'x' * 120} <!-- action:a2 -->",
        "- [ ] Reminder 2026-10-09 15:00: Call Dana <!-- action:a3 -->",  # in the note's zone
        "- [ ] Comment on o/r#7: LGTM — failed: 403 Forbidden <!-- action:a4 -->",
    ]
    assert "[Review and approve in Jarvis](https://live.example/r)" in out
    heads = [line for line in out.splitlines() if line.startswith("## ")]
    assert heads.index("## Proposed actions") == heads.index("## Transcript") - 1


def test_only_rejected_or_expired_proposals_leave_no_section() -> None:
    gone = [NoteProposal("a1", "task.create", {"text": "No"}, "rejected")]
    assert "## Proposed actions" not in render_note(NOTE, META, LINES, proposals=gone)


def test_without_a_review_link_the_proposals_are_still_listed() -> None:
    out = render_note(
        NOTE,
        META,
        LINES,
        proposals=[NoteProposal("a1", "task.create", {"text": "Yes"}, "proposed")],
    )
    assert _proposed(out) == ["- [ ] Task: Yes <!-- action:a1 -->"]
    assert "Review and approve" not in out
