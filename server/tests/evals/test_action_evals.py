"""Live action-proposal precision. Run with ``uv run pytest -q tests/evals/test_action_evals.py
--live`` against a real LiteLLM (see test_copilot_evals.py).

This measures and reports; it never fails on a low number. The synthetic cases carry an answer
key. Recorded sessions go in ``tests/evals/private/`` (git-ignored; ``jarvis-live
export-transcript <session>`` > ``<name>.txt``), and the proposals they produce are written to
``private/unlabelled.jsonl`` for you to mark ``accept`` / ``accept_edited`` / ``reject`` in
``private/labels.jsonl``. A case fails only if the LLM cannot be reached or answers off-schema."""

import httpx
import pytest

from jarvis_live.config import Settings
from jarvis_live.llm.client import LiteLLMClient
from tests.evals.actions import REPORT, SYNTHETIC, ProposalCase, load_private_cases, propose

pytestmark = pytest.mark.live

CASES = [*SYNTHETIC, *load_private_cases()]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
async def test_action_case(case: ProposalCase) -> None:
    cfg = Settings()
    assert cfg.litellm_base_url, "set JARVIS_LIVE_LITELLM_BASE_URL"
    async with httpx.AsyncClient() as http:
        llm = LiteLLMClient(cfg.litellm_base_url, http, api_key_file=cfg.litellm_api_key_file)
        shown = await propose(llm, case, cfg)  # LLMError = it could not answer in the schema
    for s in shown:
        REPORT.add(case, s)
