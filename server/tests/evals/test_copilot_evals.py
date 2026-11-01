"""Live copilot evals. Run with ``uv run pytest -q tests/evals --live`` against a real LiteLLM
(JARVIS_LIVE_LITELLM_BASE_URL, optionally JARVIS_LIVE_LITELLM_API_KEY_FILE and
JARVIS_LIVE_COPILOT_MODEL). Prompts are not tuned to pass: failures are reported as they are.

To compare a small model with Flash, point the URL and the model at it and pace the run if it has
another caller to protect: JARVIS_LIVE_EVAL_PAUSE_S waits that long after each case."""

import httpx
import pytest

from jarvis_live.config import Settings
from jarvis_live.llm.client import LiteLLMClient
from tests.evals.harness import REPORT, Outcome, check, run_case
from tests.evals.transcripts import CASES, SMALL_MODEL_CASES, Case

pytestmark = pytest.mark.live


@pytest.mark.parametrize("case", [*CASES, *SMALL_MODEL_CASES], ids=lambda c: c.name)
async def test_copilot_case(case: Case) -> None:
    cfg = Settings()
    assert cfg.litellm_base_url, "set JARVIS_LIVE_LITELLM_BASE_URL"
    async with httpx.AsyncClient() as http:
        llm = LiteLLMClient(cfg.litellm_base_url, http, api_key_file=cfg.litellm_api_key_file)
        delta, state, seconds = await run_case(llm, case, cfg)  # LLMError = schema failure
    failures = check(case, delta, state, seconds)
    REPORT.rows.append(Outcome(case.name, seconds, delta, state.snapshot(), failures))
    assert not failures, "; ".join(failures)
