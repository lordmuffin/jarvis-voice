"""The eval harness itself, against canned LLM output (no --live needed)."""

from jarvis_live.copilot.schema import ActionUpsert, CopilotDelta
from tests.evals.harness import Outcome, Report, check, run_case
from tests.evals.transcripts import (
    CASES,
    MOVED_DEADLINE,
    NO_ACTIONS,
    PLANNING_FIRST,
    PLANNING_LONG,
    ROLLING_REPLAY,
    ROLLING_STATE,
    SMALL_MODEL_CASES,
    SOLO_BRAIN_DUMP,
    VENDOR_CALL,
    Case,
    line_at,
)
from tests.fakes import FakeLLM


def case_finds(case: Case, text: str) -> bool:
    return all(any(k in text.lower() for k in g) for g in case.actions[0].keywords)


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


async def test_asks_the_way_the_copilot_loop_asks() -> None:
    llm = FakeLLM().queue("CopilotDelta", GOOD_VENDOR)
    await run_case(llm, VENDOR_CALL)
    assert llm.calls[0].think is False


async def test_a_seeded_case_starts_from_that_state() -> None:
    llm = FakeLLM().queue("CopilotDelta", CopilotDelta())
    _, state, _ = await run_case(llm, ROLLING_STATE)
    assert [a.text for a in state.actions] == [a.text for a in ROLLING_STATE.seed.actions_upsert]
    assert ROLLING_STATE.seed.actions_upsert[0].text in llm.calls[0].user


async def test_flags_a_restated_action_as_a_duplicate() -> None:
    restated = ActionUpsert(text="Fix the staging image pull timeout", owner="Priya")
    delta = CopilotDelta(actions_upsert=[restated, ActionUpsert(text="x"), ActionUpsert(text="y")])
    delta, state, secs = await run_case(FakeLLM().queue("CopilotDelta", delta), ROLLING_STATE)
    assert any("actions (>" in f for f in check(ROLLING_STATE, delta, state, secs))


async def test_flags_a_wrong_due_date() -> None:
    wrong = CopilotDelta(actions_upsert=[ActionUpsert(id="a1", text="Send the deck", due="Monday")])
    delta, state, secs = await run_case(FakeLLM().queue("CopilotDelta", wrong), MOVED_DEADLINE)
    assert any("due 'Monday'" in f for f in check(MOVED_DEADLINE, delta, state, secs))


async def test_flags_an_action_where_nothing_was_committed() -> None:
    invented = CopilotDelta(actions_upsert=[ActionUpsert(text="Plan the team lunch")])
    delta, state, secs = await run_case(FakeLLM().queue("CopilotDelta", invented), NO_ACTIONS)
    assert any("actions (>0)" in f for f in check(NO_ACTIONS, delta, state, secs))


def test_small_model_cases_are_invented_and_wellformed() -> None:
    assert len(SMALL_MODEL_CASES) == 6
    for c in SMALL_MODEL_CASES:
        assert c.transcript.startswith("[0")
        assert "192.168." not in c.transcript and ".apj." not in c.transcript


def test_the_planning_windows_cover_the_copilot_window() -> None:
    assert line_at(PLANNING_LONG.transcript.splitlines()[-1]) <= 480
    assert line_at(PLANNING_FIRST.transcript.splitlines()[-1]) <= 300


def test_each_rolling_cycle_holds_one_new_action_at_the_end_of_its_window() -> None:
    assert len(ROLLING_REPLAY) == 8
    for case in ROLLING_REPLAY:
        lines = case.transcript.splitlines()
        end = line_at(lines[-1])
        assert line_at(lines[0]) > end - 200 - 25
        assert len(case.actions) == 1
        assert case.seed is not None
        assert all(not case_finds(case, a.text) for a in case.seed.actions_upsert)


async def test_a_rolling_cycle_that_answers_nothing_fails() -> None:
    case = ROLLING_REPLAY[3]
    delta, state, secs = await run_case(FakeLLM().queue("CopilotDelta", CopilotDelta()), case)
    assert any("missing action" in f for f in check(case, delta, state, secs))


def test_cases_are_wellformed_and_report_renders() -> None:
    assert len(CASES) == 3
    for c in CASES:
        assert c.transcript.startswith("[00:")
    assert len(VENDOR_CALL.actions) == 2
    r = Report([])
    assert len(r.render()) == 2
    assert Outcome  # imported for the live report
