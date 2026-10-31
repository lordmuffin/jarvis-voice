"""The action-precision eval machinery, against canned LLM output (no --live needed)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jarvis_live.actions.report import label_key
from jarvis_live.copilot.schema import CopilotDelta
from tests.evals.actions import (
    ActionEvalReport,
    Labels,
    ProposalCase,
    load_private_cases,
    parse_transcript,
    propose,
)
from tests.evals.transcripts import Expected
from tests.fakes import FakeLLM

TRANSCRIPT = """\
[00:03] me: Remind me tomorrow at nine to send the invoice to Dana.
[00:10] me: Add a task to call the vendor about the quote.
[00:20] me: I should probably redo the garden at some point.
"""


def delta(*items: dict[str, object]) -> CopilotDelta:
    return CopilotDelta.model_validate({"proposals": list(items)})


def ev(at: str) -> list[dict[str, str]]:
    return [{"at": at, "text": "x"}]


GOOD = delta(
    {
        "kind": "reminder.schedule",
        "title": "Send the invoice",
        "at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        "evidence": ev("00:03"),
    },
    {"kind": "task.create", "text": "Call the vendor about the quote", "evidence": ev("00:10")},
    {"kind": "task.create", "text": "Redo the garden", "evidence": ev("00:20")},
    {
        "kind": "task.create",
        "text": "Invented, never said",
        "evidence": ev("09:59"),
    },  # no such line
    {
        "kind": "vault.append",
        "path": "Notes/X.md",
        "text": "x",
        "evidence": ev("00:10"),
    },  # no vault
)
CASE = ProposalCase(
    "explicit asks",
    TRANSCRIPT,
    asks=(Expected(keywords=(("invoice",),)), Expected(keywords=(("vendor",), ("quote",)))),
)


def test_a_transcript_becomes_lines_the_evidence_can_point_at() -> None:
    lines = parse_transcript(TRANSCRIPT)
    assert [(x.start_ms, x.speaker) for x in lines] == [
        (3000, "me"),
        (10_000, "me"),
        (20_000, "me"),
    ]
    assert lines[1].text == "Add a task to call the vendor about the quote."
    assert lines[0].end_ms > lines[0].start_ms


async def test_only_what_the_owner_would_be_shown_is_measured() -> None:
    shown = await propose(FakeLLM().queue("CopilotDelta", GOOD), CASE)
    assert [s.text for s in shown] == [
        "Send the invoice",
        "Call the vendor about the quote",
        "Redo the garden",
    ]  # the ungrounded one and the unreachable vault note never reach the review queue


async def test_synthetic_cases_label_themselves_and_humans_label_the_rest(tmp_path: Path) -> None:
    shown = await propose(FakeLLM().queue("CopilotDelta", GOOD), CASE)
    report = ActionEvalReport(Labels({}))
    for s in shown:
        report.add(CASE, s)
    got = {r.text: (r.verdict, r.source) for r in report.rows}
    assert got["Send the invoice"] == ("accept", "auto")
    assert got["Call the vendor about the quote"] == ("accept", "auto")
    assert got["Redo the garden"] == ("reject", "auto")  # the case lists every real ask

    open_case = ProposalCase("recorded", TRANSCRIPT)  # not exhaustive: only you can say
    s = (await propose(FakeLLM().queue("CopilotDelta", GOOD), open_case))[2]
    key = label_key(s.kind, s.payload)
    labels = Labels({("recorded", key): "accept_edited"})
    r2 = ActionEvalReport(labels)
    r2.add(open_case, s)
    r3 = ActionEvalReport(Labels({}))
    r3.add(open_case, s)
    assert (r2.rows[0].verdict, r2.rows[0].source) == ("accept_edited", "human")
    assert (r3.rows[0].verdict, r3.rows[0].source) == (None, None)


async def test_the_report_separates_human_labels_and_lists_what_still_needs_one(
    tmp_path: Path,
) -> None:
    shown = await propose(FakeLLM().queue("CopilotDelta", GOOD), CASE)
    open_case = ProposalCase("recorded", TRANSCRIPT)
    report = ActionEvalReport(Labels({}))
    for s in shown:
        report.add(CASE, s)
        report.add(open_case, s)
    summary = report.summary()
    assert summary["auto"]["made"] == 3 and summary["auto"]["precision"] == 2 / 3
    assert summary["human"]["made"] == 0 and summary["human"]["precision"] is None
    assert summary["unlabelled"] == 3

    out = tmp_path / "report.json"
    todo = tmp_path / "unlabelled.jsonl"
    report.write(out, todo)
    assert json.loads(out.read_text())["auto"]["accepted"] == 2
    rows = [json.loads(x) for x in todo.read_text().splitlines()]
    assert len(rows) == 3 and {r["case"] for r in rows} == {"recorded"}
    assert all(r["verdict"] == "" and r["key"] and r["text"] and r["quote"] for r in rows)
    assert any("Redo the garden" in line for line in report.render())


async def test_a_bad_result_is_reported_never_enforced() -> None:
    bad = delta({"kind": "task.create", "text": "Redo the garden", "evidence": ev("00:20")})
    quiet = ProposalCase("quiet", TRANSCRIPT, exhaustive=True)
    report = ActionEvalReport(Labels({}))
    for s in await propose(FakeLLM().queue("CopilotDelta", bad), quiet):
        report.add(quiet, s)
    assert report.summary()["auto"]["precision"] == 0.0  # 0%: shown, and nothing fails


def test_labels_and_private_recordings_load_from_a_directory(tmp_path: Path) -> None:
    (tmp_path / "tuesday call.txt").write_text(TRANSCRIPT)
    (tmp_path / "labels.jsonl").write_text(
        json.dumps({"case": "tuesday call", "key": "abc", "verdict": "accept"})
        + "\n"
        + json.dumps({"case": "tuesday call", "key": "def", "verdict": ""})  # not yet judged
        + "\n"
    )
    cases = load_private_cases(tmp_path)
    assert [(c.name, c.exhaustive) for c in cases] == [("tuesday call", False)]
    assert Labels.load(tmp_path / "labels.jsonl").verdicts == {("tuesday call", "abc"): "accept"}
    assert load_private_cases(tmp_path / "missing") == []
    assert Labels.load(tmp_path / "missing.jsonl").verdicts == {}
