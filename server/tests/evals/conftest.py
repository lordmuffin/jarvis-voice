import pytest

from tests.evals.harness import REPORT


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if REPORT.rows:
        terminalreporter.section("copilot eval report")
        for line in REPORT.render():
            terminalreporter.write_line(line)
