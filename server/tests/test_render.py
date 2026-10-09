import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jarvis_live.copilot.schema import FinalAction, FinalNote
from jarvis_live.copilot.transcript import Line
from jarvis_live.finalize.render import NoteMeta, render_filename, render_note, slugify

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
        "## Decisions",
        "## Actions",
        "## Open questions",
        "## Follow-ups",
        "## Related",
        "## Transcript",
    ]


def test_filename_template_and_slug() -> None:
    assert (
        render_filename("{date} {time} - {slug}.md", META.created, NOTE.title, "sid")
        == "2026-10-09 0930 - Acme renewal pricing & SLA.md"
    )
    assert slugify('a/b: "c"?') == "a b c"
    assert slugify("x" * 100) == "x" * 60
    assert slugify("///") == "untitled"
    assert render_filename("{session_id}/{slug}.md", META.created, "T", "sid") == "sid-T.md"
