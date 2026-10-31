"""Live copilot evals. Run with ``uv run pytest -q tests/evals --live`` against a real LiteLLM
(JARVIS_LIVE_LITELLM_BASE_URL, optionally JARVIS_LIVE_LITELLM_API_KEY_FILE and
JARVIS_LIVE_COPILOT_MODEL). Prompts are not tuned to pass: failures are reported as they are."""

import httpx
import pytest

from jarvis_live.config import Settings
from jarvis_live.llm.client import LiteLLMClient
from tests.evals.harness import REPORT, Outcome, check, run_case
from tests.evals.transcripts import CASES, Case

pytestmark = pytest.mark.live


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
async def test_copilot_case(case: Case) -> None:
    cfg = Settings()
    assert cfg.litellm_base_url, "set JARVIS_LIVE_LITELLM_BASE_URL"
    async with httpx.AsyncClient() as http:
        llm = LiteLLMClient(cfg.litellm_base_url, http, api_key_file=cfg.litellm_api_key_file)
        delta, state, seconds = await run_case(llm, case, cfg)  # LLMError = schema failure
    failures = check(case, delta, state, seconds)
    REPORT.rows.append(Outcome(case.name, seconds, delta, state.snapshot(), failures))
    assert not failures, "; ".join(failures)
