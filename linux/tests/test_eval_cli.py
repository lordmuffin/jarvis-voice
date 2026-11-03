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


def test_score_runs_the_engine_on_the_default_eval_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from jarvis_desktop.stt import score as score_mod

    seen: dict[str, object] = {}

    def fake_score(engine: object, eval_dir: Path, *, limit: int | None = None) -> object:
        seen.update(engine=engine, eval_dir=eval_dir, limit=limit)
        return score_mod.Report(engine="fake", clips=2, audio_s=10.0, wer=0.05)

    monkeypatch.setattr(score_mod, "score", fake_score)
    monkeypatch.setattr(app, "load_engine", lambda name, env: f"engine:{name}")
    code = app.main(["eval", "score", "--limit", "2"], env=env_for(tmp_path))
    assert code == 0
    assert seen == {
        "engine": "engine:whisper-turbo",
        "eval_dir": tmp_path / "data" / "jarvis-voice" / "eval",
        "limit": 2,
    }
    assert "disagreement with the server" in capsys.readouterr().out


def test_score_without_a_model_says_how_to_install_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = app.main(["eval", "score"], env=env_for(tmp_path))
    assert code == 1
    assert "jarvis-voice models install" in capsys.readouterr().err


def test_models_install_fetches_every_pinned_model_into_the_default_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from jarvis_desktop.stt import models

    installed: list[tuple[str, Path]] = []

    def fake_install(spec: models.ModelSpec, root: Path, **_: object) -> Path:
        installed.append((spec.name, root))
        return root / spec.name

    monkeypatch.setattr(models, "install", fake_install)
    assert app.main(["models", "install"], env=env_for(tmp_path)) == 0
    root = tmp_path / "data" / "jarvis-voice" / "models"
    assert installed == [(spec.name, root) for spec in models.ALL]
    assert models.WHISPER_TURBO_Q5.name in capsys.readouterr().out


def test_record_walks_the_shipped_sentences_into_the_read_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from jarvis_desktop import readset

    seen: dict[str, object] = {}

    def fake_record(ps: list[str], out: Path, recorder: object, **_: object) -> int:
        seen.update(count=len(ps), out=out, cmd=getattr(recorder, "cmd", None))
        return 2

    monkeypatch.setattr(readset, "record", fake_record)
    monkeypatch.setattr(readset, "find_tool", lambda: "pw-record")
    code = app.main(["eval", "record", "--source", "my-mic"], env=env_for(tmp_path))
    assert code == 0
    assert seen["count"] == 40
    assert seen["out"] == tmp_path / "data" / "jarvis-voice" / "eval-read"
    assert seen["cmd"] == [
        "pw-record",
        "--rate",
        "16000",
        "--channels",
        "1",
        "--format",
        "s16",
        "--target",
        "my-mic",
        "-",
    ]
    assert "jarvis-voice eval score --eval-dir" in capsys.readouterr().out


def test_score_can_pick_parakeet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis_desktop.stt import score as score_mod

    monkeypatch.setattr(
        score_mod, "score", lambda engine, eval_dir, **_: score_mod.Report(engine=str(engine))
    )
    monkeypatch.setattr(app, "load_engine", lambda name, env: f"engine:{name}")
    assert app.main(["eval", "score", "--engine", "parakeet"], env=env_for(tmp_path)) == 0


def test_load_engine_builds_each_engine_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis_desktop.stt import engine as engine_mod

    made: list[tuple[str, Path]] = []
    monkeypatch.setattr(engine_mod, "WhisperCppEngine", lambda m, **_: made.append(("w", m)) or "W")
    monkeypatch.setattr(engine_mod, "ParakeetEngine", lambda m, **_: made.append(("p", m)) or "P")
    env = env_for(tmp_path)
    root = tmp_path / "data" / "jarvis-voice" / "models"
    assert app.load_engine("whisper-turbo", env) == "W"
    assert app.load_engine("parakeet", env) == "P"
    assert made == [
        ("w", root / "whisper-large-v3-turbo-q5_0" / "ggml-large-v3-turbo-q5_0.bin"),
        ("p", root / "parakeet-tdt-0.6b-v2-int8"),
    ]
