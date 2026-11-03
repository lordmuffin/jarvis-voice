import tomllib
from pathlib import Path

import pytest

import jarvis_desktop
from jarvis_desktop import app

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_version_matches_pyproject() -> None:
    declared = tomllib.loads(PYPROJECT.read_text())["project"]["version"]
    assert jarvis_desktop.__version__ == declared


def test_reuses_the_headless_capture_client() -> None:
    from jarvis_live.client import control

    assert callable(control.request)


def test_cli_prints_its_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        app.main(["--version"])
    assert exit_.value.code == 0
    assert capsys.readouterr().out.strip() == f"jarvis-voice {jarvis_desktop.__version__}"
