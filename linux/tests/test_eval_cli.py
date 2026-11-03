"""`jarvis-voice eval pull`: where it gets the server and token, and what it prints."""

from pathlib import Path

import httpx
import pytest

from jarvis_desktop import app, evalset

TOKEN = "dev-token-not-a-secret-0123456789"


@pytest.fixture
def fake_pull(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    seen: dict[str, object] = {}

    async def pull(client: httpx.AsyncClient, out: Path, **kw: object) -> evalset.Summary:
        seen["base_url"] = str(client.base_url)
        seen["auth"] = client.headers.get("authorization")
        seen["out"] = out
        seen.update(kw)
        return evalset.Summary(clips=3, skipped=1, sessions=2)

    monkeypatch.setattr(evalset, "pull", pull)
    return seen


def env_for(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        **extra,
    }


def test_pull_uses_flags_and_prints_a_summary_without_the_token(
    tmp_path: Path, fake_pull: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n")
    code = app.main(
        [
            "eval",
            "pull",
            "--server",
            "https://live.example",
            "--token-file",
            str(token_file),
            "--max-clips",
            "50",
            "--mode",
            "any",
        ],
        env=env_for(tmp_path),
    )
    out, err = capsys.readouterr()
    assert code == 0
    assert fake_pull["base_url"] == "https://live.example"
    assert fake_pull["auth"] == f"Bearer {TOKEN}"
    assert fake_pull["max_clips"] == 50
    assert fake_pull["modes"] == frozenset({"solo", "meeting"})
    assert fake_pull["out"] == tmp_path / "data" / "jarvis-voice" / "eval"
    assert "3 new clips from 2 sessions (1 already there)" in out
    assert TOKEN not in out + err


def test_pull_falls_back_to_the_capture_client_config(
    tmp_path: Path, fake_pull: dict[str, object]
) -> None:
    env = env_for(tmp_path, JARVIS_LIVE_SERVER="https://lab.example", JARVIS_LIVE_TOKEN=TOKEN)
    assert app.main(["eval", "pull"], env=env) == 0
    assert fake_pull["base_url"] == "https://lab.example"
    assert fake_pull["modes"] == frozenset({"solo"})


def test_pull_without_a_token_says_how_to_give_one(
    tmp_path: Path, fake_pull: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    code = app.main(["eval", "pull", "--server", "https://live.example"], env=env_for(tmp_path))
    assert code == 2
    assert "--token-file" in capsys.readouterr().err
    assert "base_url" not in fake_pull


def test_pull_errors_are_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def failing(*_: object, **__: object) -> evalset.Summary:
        raise evalset.PullError("the server rejected the device token (401 on /v1/sessions)")

    monkeypatch.setattr(evalset, "pull", failing)
    env = env_for(tmp_path, JARVIS_LIVE_SERVER="https://live.example", JARVIS_LIVE_TOKEN=TOKEN)
    assert app.main(["eval", "pull"], env=env) == 1
    assert "rejected the device token" in capsys.readouterr().err
