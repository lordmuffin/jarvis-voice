"""The whisper.cpp engine (the default, plan section 2.2) and the fallback to Parakeet."""

import json
import os
import shutil
import stat
import struct
import sys
import textwrap
import time
from pathlib import Path

import numpy as np
import pytest

from jarvis_desktop.stt import models
from jarvis_desktop.stt.engine import Engine, EngineError, FallbackEngine, WhisperCppEngine

FAKE_CLI = textwrap.dedent(
    """\
    #!{python}
    import json, os, stat, sys, wave
    args = sys.argv[1:]
    log = os.environ["FAKE_WHISPER_LOG"]
    wav = args[args.index("-f") + 1]
    with wave.open(wav) as w:
        info = {{"channels": w.getnchannels(), "width": w.getsampwidth(), "rate": w.getframerate(),
                "frames": w.readframes(w.getnframes()).hex()}}
    info["args"] = args
    info["file_mode"] = stat.S_IMODE(os.stat(wav).st_mode)
    info["dir_mode"] = stat.S_IMODE(os.stat(os.path.dirname(wav)).st_mode)
    info["wav"] = wav
    open(log, "w").write(json.dumps(info))
    if os.environ.get("FAKE_WHISPER_FAIL"):
        sys.stderr.write("ggml_vulkan: device lost\\n")
        sys.exit(3)
    print("  Hello there,")
    print(" general Kenobi.  ")
    """
)


@pytest.fixture
def fake_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cli = tmp_path / "bin" / "whisper-cli"
    cli.parent.mkdir()
    cli.write_text(FAKE_CLI.format(python=sys.executable))
    cli.chmod(0o755)
    monkeypatch.setenv("FAKE_WHISPER_LOG", str(tmp_path / "log.json"))
    return cli


@pytest.fixture
def model(tmp_path: Path) -> Path:
    m = tmp_path / "models" / "ggml-large-v3-turbo-q5_0.bin"
    m.parent.mkdir()
    m.write_bytes(b"ggml")
    return m


def logged(tmp_path: Path) -> dict[str, object]:
    data: dict[str, object] = json.loads((tmp_path / "log.json").read_text())
    return data


def test_whisper_is_an_engine(fake_cli: Path, model: Path) -> None:
    engine = WhisperCppEngine(model, cli=fake_cli)
    assert isinstance(engine, Engine)
    assert engine.name == "whisper-large-v3-turbo-q5_0"


def test_passes_the_audio_as_a_16k_mono_wav_and_joins_the_text(
    fake_cli: Path, model: Path, tmp_path: Path
) -> None:
    pcm = struct.pack("<4h", 1, -2, 3, -4)
    assert WhisperCppEngine(model, cli=fake_cli, threads=6).transcribe(pcm) == (
        "Hello there, general Kenobi."
    )
    info = logged(tmp_path)
    assert (info["channels"], info["width"], info["rate"]) == (1, 2, 16000)
    assert info["frames"] == pcm.hex()
    args = info["args"]
    assert isinstance(args, list)
    assert args[args.index("-m") + 1] == str(model)
    assert args[args.index("-l") + 1] == "en"
    assert args[args.index("-t") + 1] == "6"
    assert "-nt" in args and "-np" in args


def test_the_audio_file_is_private_and_removed_afterwards(
    fake_cli: Path, model: Path, tmp_path: Path
) -> None:
    WhisperCppEngine(model, cli=fake_cli).transcribe(bytes(320))
    info = logged(tmp_path)
    assert info["file_mode"] == 0o600
    assert info["dir_mode"] == 0o700
    assert not Path(str(info["wav"])).exists()


def test_no_audio_runs_nothing(fake_cli: Path, model: Path, tmp_path: Path) -> None:
    assert WhisperCppEngine(model, cli=fake_cli).transcribe(b"") == ""
    assert not (tmp_path / "log.json").exists()


def test_a_failing_run_is_an_engine_error_with_its_message(
    fake_cli: Path, model: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_WHISPER_FAIL", "1")
    with pytest.raises(EngineError, match="device lost"):
        WhisperCppEngine(model, cli=fake_cli).transcribe(bytes(320))


def test_a_torn_sample_is_refused(fake_cli: Path, model: Path) -> None:
    with pytest.raises(EngineError, match="whole 16-bit samples"):
        WhisperCppEngine(model, cli=fake_cli).transcribe(b"\x00\x01\x02")


def test_a_missing_model_or_cli_says_what_to_install(
    fake_cli: Path, model: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(EngineError, match="models install"):
        WhisperCppEngine(tmp_path / "nope.bin", cli=fake_cli)
    monkeypatch.delenv("JARVIS_WHISPER_CLI", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(EngineError, match="whisper.cpp built with Vulkan"):
        WhisperCppEngine(model)


def test_the_cli_comes_from_the_environment_then_path(
    fake_cli: Path, model: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JARVIS_WHISPER_CLI", str(fake_cli))
    assert WhisperCppEngine(model).cli == fake_cli
    monkeypatch.delenv("JARVIS_WHISPER_CLI")
    monkeypatch.setenv("PATH", str(fake_cli.parent) + os.pathsep + os.environ["PATH"])
    assert WhisperCppEngine(model).cli == fake_cli


def test_a_hung_run_times_out(model: Path, tmp_path: Path) -> None:
    sleeper = tmp_path / "sleepy-cli"
    sleeper.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    sleeper.chmod(sleeper.stat().st_mode | stat.S_IXUSR)
    engine = WhisperCppEngine(model, cli=sleeper, timeout=0.5)
    start = time.monotonic()
    with pytest.raises(EngineError, match="timed out"):
        engine.transcribe(bytes(320))
    assert time.monotonic() - start < 5


# --- fallback --------------------------------------------------------------------------------


class Scripted:
    def __init__(self, name: str, text: str | None) -> None:
        self.name, self.text, self.calls = name, text, 0

    def transcribe(self, pcm: bytes) -> str:
        self.calls += 1
        if self.text is None:
            raise EngineError(f"{self.name} broke")
        return self.text


def test_the_fallback_answers_when_the_primary_fails() -> None:
    primary, backup = Scripted("whisper", None), Scripted("parakeet", "backup text")
    reasons: list[str] = []
    engine = FallbackEngine(primary, backup, on_fallback=reasons.append)
    assert engine.transcribe(b"\0\0") == "backup text"
    assert reasons == ["whisper broke"]
    assert engine.name == "whisper"


def test_the_primary_is_used_while_it_works() -> None:
    primary, backup = Scripted("whisper", "primary text"), Scripted("parakeet", "x")
    engine = FallbackEngine(primary, backup)
    assert engine.transcribe(b"\0\0") == "primary text"
    assert backup.calls == 0


def test_after_a_failure_the_primary_rests_before_it_is_tried_again() -> None:
    """A lost GPU shouldn't cost a failed attempt on every dictation."""
    now = [0.0]
    primary, backup = Scripted("whisper", None), Scripted("parakeet", "backup")
    engine = FallbackEngine(primary, backup, retry_after=60, clock=lambda: now[0])
    engine.transcribe(b"\0\0")
    engine.transcribe(b"\0\0")
    assert primary.calls == 1
    now[0] = 61
    primary.text = "back"
    assert engine.transcribe(b"\0\0") == "back"


def test_both_failing_is_an_engine_error() -> None:
    engine = FallbackEngine(Scripted("whisper", None), Scripted("parakeet", None))
    with pytest.raises(EngineError, match="parakeet broke"):
        engine.transcribe(b"\0\0")


# --- the real thing -----------------------------------------------------------------------


REAL_CLI = os.environ.get("JARVIS_WHISPER_CLI") or shutil.which("whisper-cli")
REAL_MODEL = models.models_dir() / models.WHISPER_TURBO_Q5.name / models.WHISPER_TURBO_Q5.files[0]


@pytest.mark.model
@pytest.mark.skipif(
    not REAL_CLI or not models.is_installed(models.WHISPER_TURBO_Q5, models.models_dir()),
    reason="needs whisper-cli (JARVIS_WHISPER_CLI or PATH) and the installed turbo model",
)
def test_real_whisper_decodes_ten_seconds_inside_the_release_budget() -> None:
    engine = WhisperCppEngine(REAL_MODEL)
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 300, 16_000 * 10).astype("<i2").tobytes()
    engine.transcribe(noise)  # warm the driver
    start = time.perf_counter()
    engine.transcribe(noise)
    assert time.perf_counter() - start < 0.7, "model load + decode, as whisper-cli runs it"
