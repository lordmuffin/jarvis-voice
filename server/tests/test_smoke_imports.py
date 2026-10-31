"""Baseline safety net (TDD.md §7 Phase 2): every module imports, the CLI answers --help."""

import importlib
import pkgutil
import subprocess
import sys

import pytest

import jarvis_live

MODULES = sorted(m.name for m in pkgutil.walk_packages(jarvis_live.__path__, prefix="jarvis_live."))


def test_package_has_modules_to_smoke_test() -> None:
    assert len(MODULES) > 20


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    assert importlib.import_module(name) is not None


def test_cli_help_exits_cleanly() -> None:
    out = subprocess.run(
        [sys.executable, "-m", "jarvis_live.cli", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0
    assert "serve" in out.stdout
