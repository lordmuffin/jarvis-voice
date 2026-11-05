"""Live ask evals: ``uv run pytest -q tests/evals/test_ask_evals.py --live``, against a real
LiteLLM (JARVIS_LIVE_LITELLM_BASE_URL) and model (JARVIS_LIVE_VOICE_MODEL, else the copilot's).
The bar is in ``ask_cases``. Each case runs three times, and times are printed, not judged."""

import time

import httpx
import pytest

from jarvis_live.config import Settings
from jarvis_live.llm.client import LiteLLMClient, LLMError
from jarvis_live.voice.ask_pass import ASK_SYSTEM, AskAnswer
from tests.evals.ask_cases import ASK_CASES, RUNS, AskCase, eval_user, grade

pytestmark = pytest.mark.live


@pytest.mark.parametrize("case", ASK_CASES, ids=lambda c: c.name)
async def test_ask_case(case: AskCase) -> None:
    cfg = Settings()
    assert cfg.litellm_base_url, "set JARVIS_LIVE_LITELLM_BASE_URL"
    model = cfg.voice_model or cfg.copilot_model
    user = eval_user(case)
    passed = 0
    async with httpx.AsyncClient() as http:
        llm = LiteLLMClient(cfg.litellm_base_url, http, api_key_file=cfg.litellm_api_key_file)
        for run in range(RUNS):
            began = time.monotonic()
            try:
                answer = await llm.complete_json(
                    model=model,
                    system=ASK_SYSTEM,
                    user=user,
                    schema=AskAnswer,
                    timeout_s=cfg.voice_ask_timeout_s,
                    think=False,
                )
                failures = grade(case, answer.reply)
            except LLMError as e:
                failures = [str(e)]
            seconds = time.monotonic() - began
            print(f"{case.name} run {run + 1} on {model}: {seconds:.2f} s {failures or 'ok'}")
            passed += not failures
    need = RUNS if case.critical else 2
    assert passed >= need, f"{passed} of {RUNS}"
