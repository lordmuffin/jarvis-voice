"""Live ask evals: ``uv run pytest -q tests/evals/test_ask_evals.py --live``, against a real
LiteLLM (JARVIS_LIVE_LITELLM_BASE_URL) and model (JARVIS_LIVE_VOICE_MODEL, else the copilot's).
``--ask-variant`` picks the request (``server`` by default; ``ask_cases.ASK_VARIANTS``).

The bar is in ``ask_cases``. Each case runs three times, and times are printed, not judged. The
date set runs the same way, and only prints: its result never fails a test or the bar."""

import time
from collections.abc import Callable

import httpx
import pytest

from jarvis_live.config import Settings
from jarvis_live.llm.client import LiteLLMClient, LLMError
from jarvis_live.voice.ask_pass import ASK_SYSTEM, AskAnswer
from tests.evals.ask_cases import (
    ASK_CASES,
    ASK_REPORT,
    ASK_VARIANTS,
    DATE_CASES,
    DATE_NOW,
    RUNS,
    AskCase,
    AskVariant,
    DateCase,
    eval_user,
    grade,
    grade_date,
)

pytestmark = pytest.mark.live


@pytest.fixture
def variant(request: pytest.FixtureRequest) -> AskVariant:
    name = request.config.getoption("--ask-variant")
    assert name in ASK_VARIANTS, f"--ask-variant {name!r}: one of {list(ASK_VARIANTS)}"
    ASK_REPORT.variant = name
    return ASK_VARIANTS[name]


async def _runs(kind: str, name: str, user: str, check: Callable[[str], list[str]]) -> int:
    cfg = Settings()
    assert cfg.litellm_base_url, "set JARVIS_LIVE_LITELLM_BASE_URL"
    model = cfg.voice_model or cfg.copilot_model
    passed = 0
    async with httpx.AsyncClient() as http:
        llm = LiteLLMClient(cfg.litellm_base_url, http, api_key_file=cfg.litellm_api_key_file)
        for run in range(RUNS):
            began = time.monotonic()
            reply = ""
            try:
                answer = await llm.complete_json(
                    model=model,
                    system=ASK_SYSTEM,
                    user=user,
                    schema=AskAnswer,
                    timeout_s=cfg.voice_ask_timeout_s,
                    think=False,
                )
                reply = answer.reply
                failures = check(reply)
            except LLMError as e:
                failures = [str(e)]
            seconds = time.monotonic() - began
            words = len(reply.split())
            print(f"{kind} {name} run {run + 1} on {model}: {seconds:.2f} s {failures or 'ok'}")
            ASK_REPORT.add(kind, name, ok=not failures, seconds=seconds, words=words)
            passed += not failures
    return passed


@pytest.mark.parametrize("case", ASK_CASES, ids=lambda c: c.name)
async def test_ask_case(case: AskCase, variant: AskVariant) -> None:
    passed = await _runs("bar", case.name, eval_user(case, variant), lambda r: grade(case, r))
    need = RUNS if case.critical else 2
    assert passed >= need, f"{passed} of {RUNS}"


@pytest.mark.parametrize("case", DATE_CASES, ids=lambda c: c.name)
async def test_date_case(case: DateCase, variant: AskVariant) -> None:
    """Reported beside the bar, never asserted."""
    user = eval_user(case, variant, DATE_NOW)
    await _runs("dates", case.name, user, lambda r: grade_date(case, r))
