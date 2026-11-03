"""Recording a read eval set: you read known sentences, so the reference text is exact."""

import json
import struct
import sys
import wave
from collections.abc import Iterator
from pathlib import Path

import pytest

from jarvis_desktop import evalset, readset
from jarvis_desktop.readset import SubprocessRecorder, clip_id, prompts, record, trim_silence

RATE = 16_000


def tone(ms: int, amplitude: int = 8000) -> bytes:
    return struct.pack(f"<{ms * 16}h", *([amplitude, -amplitude] * (ms * 8)))


def silence(ms: int) -> bytes:
    return bytes(ms * 32)


# --- sentences --------------------------------------------------------------------------------


def test_ships_forty_sentences_with_digits_for_numbers() -> None:
    ps = prompts()
    assert len(ps) == 40
    assert len(set(ps)) == 40
    assert any(any(c.isdigit() for c in p) for p in ps)
    assert all(p == p.strip() and p for p in ps)


def test_clip_ids_change_when_the_sentence_changes() -> None:
    assert clip_id(3, "Hello there.") == clip_id(3, "Hello there.")
    assert clip_id(3, "Hello there.") != clip_id(3, "Hello their.")
    assert clip_id(3, "Hello there.").startswith("read-03-")


# --- trimming --------------------------------------------------------------------------------


def test_trims_leading_and_trailing_silence_keeping_a_pad() -> None:
    pcm = silence(1_000) + tone(2_000) + silence(1_500)
    out = trim_silence(pcm, pad_ms=200)
    assert len(out) // 32 == pytest.approx(2_400, abs=40)


def test_all_silence_trims_to_nothing() -> None:
    assert trim_silence(silence(2_000)) == b""


def test_speech_to_the_edges_is_kept_whole() -> None:
    pcm = tone(1_000)
    assert trim_silence(pcm) == pcm


def test_a_torn_sample_is_dropped() -> None:
    assert len(trim_silence(tone(500) + b"\x01")) % 2 == 0


# --- the recorder ----------------------------------------------------------------------------


def test_subprocess_recorder_collects_what_the_command_writes() -> None:
    writer = (
        "import sys, time\n"
        "while True:\n"
        "    sys.stdout.buffer.write(b'\\x01\\x00' * 1600); sys.stdout.flush(); time.sleep(0.02)\n"
    )
    rec = SubprocessRecorder([sys.executable, "-c", writer])
    rec.start()
    import time

    time.sleep(0.3)
    pcm = rec.stop()
    assert len(pcm) >= 3200 and len(pcm) % 2 == 0
    assert set(pcm[:4]) == {0, 1}


def test_a_recorder_that_dies_says_why() -> None:
    rec = SubprocessRecorder([sys.executable, "-c", "import sys; sys.exit('no such source')"])
    rec.start()
    import time

    time.sleep(0.2)
    with pytest.raises(readset.RecordError, match="no such source"):
        rec.stop()


# --- a session -------------------------------------------------------------------------------


class FakeRecorder:
    """Each take returns the next canned recording."""

    def __init__(self, takes: list[bytes]) -> None:
        self.takes = takes
        self.started = 0

    def start(self) -> None:
        self.started += 1

    def stop(self) -> bytes:
        return self.takes.pop(0)


def keys(*answers: str) -> Iterator[str]:
    yield from answers


def run(tmp_path: Path, ps: list[str], takes: list[bytes], answers: list[str]) -> list[str]:
    said: list[str] = []
    it = keys(*answers)
    record(ps, tmp_path, FakeRecorder(takes), ask=lambda _: next(it), say=said.append)
    return said


def manifest(out: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (out / "manifest.jsonl").read_text().splitlines()]


def test_records_each_sentence_with_its_exact_reference(tmp_path: Path) -> None:
    ps = ["First sentence.", "Second sentence."]
    takes = [silence(300) + tone(1_000), tone(1_200)]
    # start, stop, keep for each sentence
    run(tmp_path, ps, takes, ["", "", "", "", "", ""])
    rows = manifest(tmp_path)
    assert [r["reference"] for r in rows] == ps
    assert all(r["source"] == "read" for r in rows)
    with wave.open(str(tmp_path / str(rows[0]["audio"])), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, RATE)


def test_redo_replaces_the_take(tmp_path: Path) -> None:
    run(tmp_path, ["Only one."], [tone(800), tone(1_600)], ["", "", "r", "", "", ""])
    [row] = manifest(tmp_path)
    assert row["duration_ms"] == pytest.approx(1_600, abs=40)


def test_a_take_with_no_speech_is_asked_for_again(tmp_path: Path) -> None:
    said = run(tmp_path, ["Only one."], [silence(1_000), tone(1_000)], ["", "", "", "", ""])
    assert any("didn't hear" in s.lower() for s in said)
    assert len(manifest(tmp_path)) == 1


def test_skip_and_quit(tmp_path: Path) -> None:
    run(tmp_path, ["One.", "Two.", "Three."], [tone(1_000)], ["s", "", "", "", "q"])
    assert [r["reference"] for r in manifest(tmp_path)] == ["Two."]


def test_a_second_run_resumes_after_what_is_already_recorded(tmp_path: Path) -> None:
    ps = ["One.", "Two."]
    run(tmp_path, ps, [tone(1_000)], ["", "", "", "q"])
    said = run(tmp_path, ps, [tone(1_000)], ["", "", ""])
    assert [r["reference"] for r in manifest(tmp_path)] == ["One.", "Two."]
    assert not any("One." in s for s in said), "the recorded sentence isn't shown again"


def test_the_read_set_is_private_and_never_in_a_checkout(tmp_path: Path) -> None:
    run(tmp_path / "set", ["One."], [tone(1_000)], ["", "", ""])
    out = tmp_path / "set"
    assert out.stat().st_mode & 0o777 == 0o700
    assert (out / "clips").stat().st_mode & 0o777 == 0o700
    for f in [out / "manifest.jsonl", *(out / "clips").iterdir()]:
        assert f.stat().st_mode & 0o077 == 0
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(evalset.PullError, match="git"):
        run(tmp_path / "repo" / "eval", ["One."], [tone(1_000)], ["", "", ""])


def test_default_output_is_beside_the_pulled_set(tmp_path: Path) -> None:
    out = readset.default_output_dir({"XDG_DATA_HOME": str(tmp_path), "HOME": "/nowhere"})
    assert out == tmp_path / "jarvis-voice" / "eval-read"


def test_says_which_mic_it_records_from(tmp_path: Path) -> None:
    said: list[str] = []
    it = keys("q")
    record(
        ["One."],
        tmp_path,
        FakeRecorder([]),
        ask=lambda _: next(it),
        say=said.append,
        mic="Revelator Dynamic",
    )
    assert any("Revelator Dynamic" in s for s in said)


def test_pure_zeros_are_reported_as_a_dead_mic_not_as_quiet(tmp_path: Path) -> None:
    said = run(tmp_path, ["Only one."], [silence(2_000), tone(1_000)], ["", "", "", "", ""])
    dead = [s for s in said if "no signal at all" in s]
    assert dead and "muted" in dead[0] and "Bluetooth" in dead[0]


def test_a_quiet_take_with_some_signal_is_reported_as_quiet(tmp_path: Path) -> None:
    hiss = tone(2_000, amplitude=40)
    said = run(tmp_path, ["Only one."], [hiss, tone(1_000)], ["", "", "", "", ""])
    assert any("too quiet" in s.lower() for s in said)
    assert not any("no signal at all" in s for s in said)


# --- which recorder ---------------------------------------------------------------------------


def test_on_linux_it_records_with_the_capture_clients_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(readset, "find_tool", lambda: "parec")
    cmd = readset.recorder_command(None, platform="linux")
    assert cmd[0] == "parec" and "--rate=16000" in cmd


def test_on_macos_it_records_the_default_input_with_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(readset.shutil, "which", lambda tool: f"/opt/homebrew/bin/{tool}")
    cmd = readset.recorder_command(None, platform="darwin")
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-f") + 1] == "avfoundation"
    assert cmd[cmd.index("-i") + 1] == ":default"
    assert ["-ac", "1"] == cmd[cmd.index("-ac") : cmd.index("-ac") + 2]
    assert ["-ar", "16000"] == cmd[cmd.index("-ar") : cmd.index("-ar") + 2]
    assert cmd[-3:] == ["-f", "s16le", "-"]


def test_on_macos_a_named_input_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readset.shutil, "which", lambda tool: f"/opt/homebrew/bin/{tool}")
    cmd = readset.recorder_command("MacBook Pro Microphone", platform="darwin")
    assert cmd[cmd.index("-i") + 1] == ":MacBook Pro Microphone"


def test_on_macos_without_ffmpeg_it_says_how_to_get_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readset.shutil, "which", lambda tool: None)
    with pytest.raises(readset.RecordError, match="brew install ffmpeg"):
        readset.recorder_command(None, platform="darwin")


def test_on_macos_the_mic_is_described_without_pactl() -> None:
    assert readset.describe_source(None, platform="darwin") == "the macOS default input"
    assert readset.describe_source("MacBook Pro Microphone", platform="darwin") == (
        "MacBook Pro Microphone"
    )
