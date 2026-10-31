import pytest

from tests.evals.actions import PRIVATE_DIR, REPORT_PATH
from tests.evals.actions import REPORT as ACTION_REPORT
from tests.evals.harness import REPORT


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
