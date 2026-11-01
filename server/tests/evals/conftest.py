import asyncio
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from tests.evals.actions import PRIVATE_DIR, REPORT_PATH
from tests.evals.actions import REPORT as ACTION_REPORT
from tests.evals.harness import REPORT


@pytest_asyncio.fixture(autouse=True)
async def _pace() -> AsyncIterator[None]:
    yield
    await asyncio.sleep(float(os.environ.get("JARVIS_LIVE_EVAL_PAUSE_S") or 0))


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if REPORT.rows:
        terminalreporter.section("copilot eval report")
        for line in REPORT.render():
            terminalreporter.write_line(line)

    if ACTION_REPORT.rows:
        terminalreporter.section("action proposal precision (reported, not enforced)")
        for line in ACTION_REPORT.render():
            terminalreporter.write_line(line)
        ACTION_REPORT.write(REPORT_PATH, PRIVATE_DIR / "unlabelled.jsonl")
        terminalreporter.write_line(f"written to {REPORT_PATH.name}")
