"""The voice eval itself, against canned LLM output (no model needed)."""

import json

import httpx
import pytest

from jarvis_live.actions.schema import ProposalIn
from jarvis_live.llm.client import LiteLLMClient
from jarvis_live.voice.schema import VoiceRequest
from jarvis_live.voice.service import VOICE_SYSTEM
from tests.evals.voice import (
    DECODE_3B_2070,
    PREFILL_3B_2070,
    VARIANTS,
    Recorder,
    Row,
    break_even,
    can_still_pass,
    card_rates,
    check,
    p95,
    project,
    run_case,
    verdict,
)
from tests.evals.voice_cases import HOMELAB, TODAY, VOICE, VOICE_CASES
from tests.fakes import FakeLLM

BY_NAME = {c.name: c for c in VOICE_CASES}
CLOUD = BY_NAME["cloud, repo named"]
NOT_CLEAR = BY_NAME["cloud, repo not clear"]
PI = BY_NAME["pi, misheard as pie"]
TASK = BY_NAME["action, task with a due day"]
REMINDER = BY_NAME["action, reminder tomorrow"]
COMMENT = BY_NAME["action, PR comment"]
TWO = BY_NAME["action, two steps"]
OFF_LIST = BY_NAME["action, repo not allowed for a comment"]
REPEAT = BY_NAME["action, already proposed"]
BACK = BY_NAME["none, scratch that"]


def good_cloud(**kw: object) -> VoiceRequest:
    fields: dict[str, object] = {
        "target": "cloud",
        "title": "Add a retry to the transcript upload",
        "task": "Add a retry to the transcript upload.",
        "repo": VOICE,
        "acceptance_criteria": ["A failed upload retries"],
        "done_when": "Uploads retry.",
    }
    return VoiceRequest.model_validate({**fields, **kw})


def row(case: str, ok: bool, run: int = 0, **kw: object) -> Row:
    fields: dict[str, object] = {"model": "m", "run": run, "case": case, "ok": ok, "fails": []}
    return Row(**{**fields, **kw})  # type: ignore[arg-type]


def test_every_target_is_asked_for_and_there_are_cases_a_wrong_answer_files() -> None:
    assert {c.target for c in VOICE_CASES} == {"cloud", "pi", "action", "none"}
    assert sum(c.critical for c in VOICE_CASES) >= 4
    assert len({c.name for c in VOICE_CASES}) == len(VOICE_CASES)


def test_a_complete_cloud_reply_passes() -> None:
    assert check(CLOUD, good_cloud()) == []


def test_the_wrong_target_fails_and_says_so() -> None:
    assert any("target" in f for f in check(CLOUD, VoiceRequest(target="pi", title="x", task="y")))


def test_a_cloud_reply_needs_the_right_repo() -> None:
    assert any("repo" in f for f in check(CLOUD, good_cloud(repo=HOMELAB)))


def test_an_unclear_repo_must_be_left_null() -> None:
    reply = good_cloud(task="Tidy the readme and fix the links.", repo=VOICE)
    assert any("repo" in f for f in check(NOT_CLEAR, reply))
    assert check(NOT_CLEAR, reply.model_copy(update={"repo": None})) == []


def test_a_cloud_reply_needs_criteria_done_when_and_a_short_title() -> None:
    assert any("criteria" in f for f in check(CLOUD, good_cloud(acceptance_criteria=[])))
    assert any("criteria" in f for f in check(CLOUD, good_cloud(acceptance_criteria=["x"] * 7)))
    assert any("done_when" in f for f in check(CLOUD, good_cloud(done_when=None)))
    assert any("title" in f for f in check(CLOUD, good_cloud(title="retry upload " * 8)))


def test_a_cloud_or_pi_reply_must_keep_what_was_asked() -> None:
    assert any("missing" in f for f in check(CLOUD, good_cloud(title="Fix it", task="Fix it.")))
    pi = VoiceRequest(target="pi", title="Look at the backup job", task="Why does it fail?")
    assert check(PI, pi) == []


def test_a_task_needs_its_words_and_its_due_day() -> None:
    def task(**kw: str) -> VoiceRequest:
        return VoiceRequest(target="action", proposals=[ProposalIn(kind="task.create", **kw)])

    assert check(TASK, task(text="Review the pricing proposal", due="Friday")) == []
    assert check(TASK, task(text="Review the pricing proposal", due="2026-11-06")) == []
    assert any("due" in f for f in check(TASK, task(text="Review the pricing proposal")))
    assert any("missing" in f for f in check(TASK, task(text="Buy milk", due="Friday")))


def test_a_reminder_must_land_on_the_day_and_hour_said() -> None:
    def rem(at: str) -> VoiceRequest:
        p = ProposalIn(kind="reminder.schedule", title="Look at the churn numbers", at=at)
        return VoiceRequest(target="action", proposals=[p])

    assert check(REMINDER, rem("2026-11-05T09:00:00")) == []
    assert any("at" in f for f in check(REMINDER, rem("2026-11-06T09:00:00")))
    assert any("at" in f for f in check(REMINDER, rem("2026-11-05T21:00:00")))


def test_a_reminder_the_server_would_refuse_is_not_filed() -> None:
    p = ProposalIn(kind="reminder.schedule", title="Churn numbers", at="tomorrow at nine")
    fails = check(REMINDER, VoiceRequest(target="action", proposals=[p]))
    assert any("refuse" in f for f in fails)


def test_a_pr_comment_needs_the_repo_and_the_number() -> None:
    def comment(pr: int, repo: str = VOICE) -> VoiceRequest:
        p = ProposalIn(kind="git.pr_comment", repo=repo, pr=pr, body="Needs a rollback note")
        return VoiceRequest(target="action", proposals=[p])

    assert check(COMMENT, comment(214)) == []
    assert any("pr" in f for f in check(COMMENT, comment(241)))
    assert any("refuse" in f for f in check(COMMENT, comment(214, HOMELAB)))


def test_an_extra_step_is_an_invention() -> None:
    extra = ProposalIn(kind="task.create", text="Tell the team")
    p = ProposalIn(kind="task.create", text="Review the pricing proposal", due="Friday")
    assert any(
        "extra" in f for f in check(TASK, VoiceRequest(target="action", proposals=[p, extra]))
    )


def test_two_steps_are_both_needed() -> None:
    task = ProposalIn(kind="task.create", text="Renew the domain")
    rem = ProposalIn(kind="reminder.schedule", title="Check the domain", at="2026-11-06T10:00:00")
    assert check(TWO, VoiceRequest(target="action", proposals=[task, rem])) == []
    assert any("missing" in f for f in check(TWO, VoiceRequest(target="action", proposals=[task])))


def test_a_comment_on_a_repo_off_the_list_may_file_nothing_whatever_the_target() -> None:
    bad = ProposalIn(kind="task.create", text="Comment on PR 12")
    assert check(OFF_LIST, VoiceRequest(target="none")) == []
    refused = ProposalIn(kind="git.pr_comment", repo=HOMELAB, pr=12, body="Looks good")
    assert check(OFF_LIST, VoiceRequest(target="action", proposals=[refused])) == []
    assert check(OFF_LIST, VoiceRequest(target="action", proposals=[bad]))
    assert check(OFF_LIST, VoiceRequest(target="cloud", title="x", task="y"))


def test_a_repeat_of_what_is_already_proposed_files_nothing() -> None:
    again = ProposalIn(kind="task.create", text="Review the pricing proposal")
    assert check(REPEAT, VoiceRequest(target="action", proposals=[again]))
    assert check(REPEAT, VoiceRequest(target="action")) == []


def test_a_reply_with_no_target_key_reads_as_none_as_the_server_would() -> None:
    assert check(BACK, VoiceRequest.model_validate({})) == []
    assert check(PI, VoiceRequest.model_validate({}))


def test_the_filed_steps_are_judged_not_a_stray_target_none_with_proposals() -> None:
    stray = ProposalIn(kind="task.create", text="Forget the churn numbers")
    assert (
        check(BACK, VoiceRequest(target="none", proposals=[stray])) == []
    )  # service files nothing


async def test_it_sends_the_real_system_prompt_schema_and_thinking_off() -> None:
    llm = FakeLLM().queue("VoiceRequest", VoiceRequest())
    await run_case(llm, CLOUD, "m", timeout_s=9.0)
    call = llm.calls[0]
    assert (call.system, call.schema, call.think) == (VOICE_SYSTEM, "VoiceRequest", False)
    assert f"Today: {TODAY}" in call.user
    assert CLOUD.said in call.user


async def test_it_tells_the_model_what_is_commentable_and_already_proposed() -> None:
    llm = FakeLLM().queue("VoiceRequest", VoiceRequest())
    await run_case(llm, REPEAT, "m", timeout_s=9.0)
    assert "task.create: Review the pricing proposal" in llm.calls[0].user
    llm = FakeLLM().queue("VoiceRequest", VoiceRequest())
    await run_case(llm, OFF_LIST, "m", timeout_s=9.0)
    assert f"REPOSITORIES YOU MAY COMMENT ON\n{VOICE}\n" in llm.calls[0].user


async def test_a_real_client_request_is_recorded_with_its_timings_and_reasoning() -> None:
    reply = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": '{"target":"none"}', "reasoning_content": "hmm"},
            }
        ],
        "usage": {"prompt_tokens": 600, "completion_tokens": 9},
        "timings": {"prompt_per_second": 100.0, "predicted_per_second": 20.0},
    }
    sent: list[dict[str, object]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=reply)

    rec = Recorder(httpx.MockTransport(answer))
    async with httpx.AsyncClient(transport=rec) as http:
        await run_case(LiteLLMClient("http://x/v1", http), BACK, "m", timeout_s=9.0)
    schema = sent[0]["response_format"]["json_schema"]  # type: ignore[index]
    assert schema["schema"] == VoiceRequest.model_json_schema()
    assert sent[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert rec.attempts == [
        {
            "prompt": 600,
            "tokens": 9,
            "finish": "stop",
            "reasoning": 3,
            "content": '{"target":"none"}',
            "prompt_rate": 100.0,
            "decode_rate": 20.0,
        }
    ]


async def test_today_is_the_default_and_sends_the_request_the_server_sends() -> None:
    assert VARIANTS["today"].schema is VoiceRequest
    llm = FakeLLM().queue("VoiceRequest", VoiceRequest())
    await run_case(llm, CLOUD, "m", timeout_s=9.0)
    assert f"Today: {TODAY}\n" in llm.calls[0].user


async def test_the_weekday_variant_names_the_day_and_changes_nothing_else() -> None:
    plain = FakeLLM().queue("VoiceRequest", VoiceRequest())
    await run_case(plain, CLOUD, "m", timeout_s=9.0)
    named = FakeLLM().queue("VoiceRequest", VoiceRequest())
    await run_case(named, CLOUD, "m", timeout_s=9.0, variant=VARIANTS["named-day"])
    assert f"Today: Wednesday {TODAY}\n" in named.calls[0].user
    assert named.calls[0].user.replace("Wednesday ", "") == plain.calls[0].user
    assert named.calls[0].system == plain.calls[0].system


def test_the_kinds_variant_requires_what_the_server_needs_for_each_spoken_kind() -> None:
    schema = VARIANTS["kinds"].schema.model_json_schema()
    kinds = {
        k["properties"]["kind"]["const"]: k
        for k in schema["properties"]["proposals"]["items"]["anyOf"]
    }
    assert set(kinds) == {"task.create", "reminder.schedule", "git.pr_comment"}
    assert kinds["task.create"]["required"] == ["kind", "text"]
    assert kinds["reminder.schedule"]["required"] == ["kind", "title", "at"]
    assert kinds["git.pr_comment"]["required"] == ["kind", "repo", "pr", "body"]
    assert "ProposalIn" not in schema.get("$defs", {})


def test_the_kinds_variant_parses_as_tolerantly_as_today() -> None:
    assert VARIANTS["kinds"].schema.model_validate({}).target == "none"
    reply = VARIANTS["kinds"].schema.model_validate(
        {"target": "action", "proposals": [{"kind": "pi.session", "text": "x"}, {"kind": "bad"}]}
    )
    assert reply.proposals == []


def all_pass() -> list[Row]:
    return [row(c.name, True, run) for run in range(3) for c in VOICE_CASES]


def test_all_three_runs_passing_clears_the_quality_bar() -> None:
    v = verdict(all_pass())
    assert v.quality_ok and v.cases_met == len(VOICE_CASES) and v.critical_ok


def test_one_miss_on_an_ordinary_case_is_allowed() -> None:
    rows = all_pass()
    rows[0] = row(rows[0].case, False, 0)
    assert verdict(rows).quality_ok
    assert can_still_pass(rows)


def test_two_misses_on_an_ordinary_case_fail_the_bar() -> None:
    rows = all_pass()
    name = next(c.name for c in VOICE_CASES if not c.critical)
    hit = 0
    for i, r in enumerate(rows):
        if r.case == name and hit < 2:
            rows[i], hit = row(name, False, r.run), hit + 1
    assert not verdict(rows).quality_ok
    assert not can_still_pass(rows)


def test_one_miss_on_a_critical_case_fails_the_bar() -> None:
    rows = all_pass()
    i = next(i for i, r in enumerate(rows) if r.case == BACK.name)
    rows[i] = row(BACK.name, False, 0)
    assert not verdict(rows).critical_ok
    assert not verdict(rows).quality_ok
    assert not can_still_pass(rows)


def test_under_nine_in_ten_runs_fails_even_if_every_case_reaches_two_of_three() -> None:
    rows = all_pass()
    missed = 0
    seen: set[str] = set()
    critical = {c.name for c in VOICE_CASES if c.critical}
    for i, r in enumerate(rows):
        if r.case not in critical and r.case not in seen:
            rows[i] = row(r.case, False, r.run)
            seen.add(r.case)
            missed += 1
    assert missed / len(rows) > 0.1
    v = verdict(rows)
    assert v.cases_met == len(VOICE_CASES) and v.critical_ok
    assert not v.quality_ok
    assert not can_still_pass(rows)


def test_an_unfinished_run_has_not_passed_yet_but_can_still() -> None:
    rows = [row(c.name, True, 0) for c in VOICE_CASES]
    assert not verdict(rows).quality_ok
    assert can_still_pass(rows)


def test_a_cut_off_reply_fails_the_bar() -> None:
    rows = all_pass()
    rows[0] = row(rows[0].case, True, 0, finish=["length"])
    assert not verdict(rows).quality_ok


def test_a_retry_is_time_the_caller_waited() -> None:
    r = row("x", True, prompts=[600, 600], tokens=[100, 100])
    assert project(r, prefill=600.0, decode=100.0) == pytest.approx(4.0)


def test_p95_is_nearest_rank_as_slo_computes_it() -> None:
    assert p95([float(n) for n in range(1, 21)]) == 19.0
    assert p95([]) == 0.0


def test_a_rate_on_the_card_scales_the_3b_by_what_the_cpu_saw() -> None:
    rates = card_rates(
        cpu_prefill=50.0, cpu_decode=10.0, control_prefill=100.0, control_decode=20.0
    )
    assert rates == pytest.approx((PREFILL_3B_2070 / 2, DECODE_3B_2070 / 2))


def test_break_even_is_the_decode_rate_that_lands_on_the_budget() -> None:
    rows = [row("x", True, prompts=[int(PREFILL_3B_2070)], tokens=[int(DECODE_3B_2070 * 4)])]
    assert break_even(rows) == pytest.approx(DECODE_3B_2070 * 2.0)
