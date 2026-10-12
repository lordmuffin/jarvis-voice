"""Baseline safety net (TDD.md §7 Phase 2) for the legacy service.

Modules with optional heavy dependencies (livekit, soundfile) are compiled instead of
imported so the check needs only requirements.txt.
"""

import importlib
import pathlib
import py_compile

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "jarvis_voice"
IMPORTABLE = ["transcribe", "vault", "vault_tools", "capture_api"]
OPTIONAL_DEPS = ["f5_tts_api", "livekit_agent"]


def test_every_module_is_covered_by_this_smoke_test() -> None:
    on_disk = {p.stem for p in SRC.glob("*.py") if p.stem != "__init__"}
    assert on_disk == set(IMPORTABLE) | set(OPTIONAL_DEPS)


@pytest.mark.parametrize("name", IMPORTABLE)
def test_module_imports(name: str) -> None:
    assert importlib.import_module(f"jarvis_voice.{name}") is not None


@pytest.mark.parametrize("name", OPTIONAL_DEPS)
def test_optional_module_compiles(name: str, tmp_path: pathlib.Path) -> None:
    py_compile.compile(str(SRC / f"{name}.py"), cfile=str(tmp_path / "x.pyc"), doraise=True)
