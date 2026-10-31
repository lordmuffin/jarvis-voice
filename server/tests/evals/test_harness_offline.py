"""The eval harness itself, against canned LLM output (no --live needed)."""

from jarvis_live.copilot.schema import ActionUpsert, CopilotDelta
from tests.evals.harness import Outcome, Report, check, run_case
from tests.evals.transcripts import CASES, SOLO_BRAIN_DUMP, VENDOR_CALL
from tests.fakes import FakeLLM

GOOD_VENDOR = CopilotDelta(
    actions_upsert=[
        ActionUpsert(text="Send revised quote with SOC 2 report", owner="Dana", due="Friday"),
        ActionUpsert(text="Return security questionnaire", owner="Marcus", due="Tuesday"),
    ]
)


async def test_passes_on_correct_output() -> None:
    delta, state, secs = await run_case(FakeLLM().queue("CopilotDelta", GOOD_VENDOR), VENDOR_CALL)
    assert check(VENDOR_CALL, delta, state, secs) == []


async def test_flags_missing_action_wrong_owner_and_slowness() -> None:
    bad = CopilotDelta(actions_upsert=[ActionUpsert(text="Send revised quote", owner="Bob")])
    delta, state, _ = await run_case(FakeLLM().queue("CopilotDelta", bad), VENDOR_CALL)
    failures = check(VENDOR_CALL, delta, state, seconds=9.0)
    assert any("missing action [questionnaire]" in f for f in failures)
    assert any("'Bob'" in f for f in failures)
    assert any("round-trip" in f for f in failures)


async def test_flags_invented_owner() -> None:
    bad = CopilotDelta(
        actions_upsert=[
            ActionUpsert(text="Renew passport", owner="me"),
            ActionUpsert(text="Book the dentist"),
        ]
    )
    delta, state, secs = await run_case(FakeLLM().queue("CopilotDelta", bad), SOLO_BRAIN_DUMP)
    failures = check(SOLO_BRAIN_DUMP, delta, state, secs)
    assert any("invented owner 'me'" in f for f in failures)


def test_cases_are_wellformed_and_report_renders() -> None:
    assert len(CASES) == 3
    for c in CASES:
        assert c.transcript.startswith("[00:")
    assert len(VENDOR_CALL.actions) == 2
    r = Report([])
    assert len(r.render()) == 2
    assert Outcome  # imported for the live report
