"""The speaker: Piper's audio into a PipeWire stream pinned to the sink the gate approved.

The player is a real child process (a small Python script standing in for ``pw-cat``), the
synthesiser a fake, and the route a scripted sequence. One test runs the real Piper voice and is
skipped unless it and the ``speech`` extra are installed (``model`` marker)."""

import asyncio
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from jarvis_desktop import speech
from jarvis_desktop.route import Route, Sink
from jarvis_desktop.speech import PipeWireSpeaker, Played, SpeechError, speak_guarded

HEADPHONES = Sink("alsa_output.jack", "alsa", "pci", None, "analog-stereo", "headphones", "yes")
SPEAKERS = Sink("alsa_output.jack", "alsa", "pci", None, "analog-stereo", "speaker", "yes")
OTHER = Sink("bluez_output.other", "bluez5", "bluetooth", "headset", "a2dp-sink", None, None)


class FakeSynth:
    sample_rate = 22050

    def __init__(self, chunks: list[bytes] | None = None, fail: bool = False) -> None:
        self._chunks = chunks if chunks is not None else [b"\x01\x00" * 100, b"\x02\x00" * 100]
        self.fail = fail
        self.texts: list[str] = []

    def chunks(self, text: str) -> Iterator[bytes]:
        self.texts.append(text)
        if self.fail:
            raise RuntimeError("synthesis broke")
        yield from self._chunks


def recorder(out: Path, *, linger: float = 0.0, code: int = 0) -> list[str]:
    """A player that writes what it is given to ``out``, waits ``linger`` s, exits ``code``."""
    script = (
        "import sys, time\n"
        f"data = sys.stdin.buffer.read()\nopen({str(out)!r}, 'wb').write(data)\n"
        f"time.sleep({linger})\nsys.exit({code})\n"
    )
    return [sys.executable, "-c", script]


def speaker_with(synth: FakeSynth, player: list[str]) -> tuple[PipeWireSpeaker, list[tuple]]:
    calls: list[tuple] = []

    def command(sink: str, rate: int) -> list[str]:
        calls.append((sink, rate))
        return player

    return PipeWireSpeaker(lambda: synth, player=command), calls


def test_the_player_is_pw_cat_pinned_to_the_sink_and_never_moved() -> None:
    argv = speech.player_command("alsa_output.jack", 22050)
    assert argv[:2] == ["pw-cat", "--playback"]
    assert argv[argv.index("--target") + 1] == "alsa_output.jack"
    assert argv[argv.index("--rate") + 1] == "22050"
    assert argv[argv.index("--channels") + 1] == "1"
    assert argv[argv.index("--format") + 1] == "s16"
    assert "--raw" in argv and argv[-1] == "-"
    props = argv[argv.index("--properties") + 1]
    # Without this, WirePlumber moves the stream to the new default when the headphones go.
    assert "node.dont-reconnect = true" in props


async def test_speaks_every_chunk_into_the_player(tmp_path: Path) -> None:
    out = tmp_path / "played.raw"
    synth = FakeSynth()
    spk, calls = speaker_with(synth, recorder(out))
    first: list[float] = []
    await spk.speak("Hello there.", "alsa_output.jack", lambda: first.append(time.monotonic()))
    assert out.read_bytes() == b"\x01\x00" * 100 + b"\x02\x00" * 100
    assert calls == [("alsa_output.jack", 22050)]
    assert synth.texts == ["Ready.", "Hello there."], "warmed on first use"
    assert len(first) == 1


async def test_speak_returns_when_the_player_has_drained(tmp_path: Path) -> None:
    spk, _ = speaker_with(FakeSynth(), recorder(tmp_path / "o", linger=0.3))
    began = time.monotonic()
    await spk.speak("Hi.", "s", lambda: None)
    assert time.monotonic() - began >= 0.3


async def test_cancelling_stops_the_player_at_once(tmp_path: Path) -> None:
    spk, _ = speaker_with(FakeSynth(), recorder(tmp_path / "o", linger=30))
    task = asyncio.create_task(spk.speak("Hi.", "s", lambda: None))
    await asyncio.sleep(0.3)
    began = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - began < 1.0
    assert spk.playing is None, "the player was reaped"


async def test_a_player_that_fails_is_a_speech_error(tmp_path: Path) -> None:
    spk, _ = speaker_with(FakeSynth(), recorder(tmp_path / "o", code=3))
    with pytest.raises(SpeechError):
        await spk.speak("Hi.", "s", lambda: None)


async def test_a_missing_player_is_a_speech_error() -> None:
    spk, _ = speaker_with(FakeSynth(), ["/nonexistent/pw-cat"])
    with pytest.raises(SpeechError):
        await spk.speak("Hi.", "s", lambda: None)


async def test_a_synthesis_failure_is_a_speech_error_and_plays_nothing(tmp_path: Path) -> None:
    out = tmp_path / "o"
    first: list[int] = []
    spk, _ = speaker_with(FakeSynth(fail=True), recorder(out))
    with pytest.raises(SpeechError):
        await spk.speak("Hi.", "s", lambda: first.append(1))
    assert first == []


async def test_silence_is_a_speech_error(tmp_path: Path) -> None:
    spk, _ = speaker_with(FakeSynth(chunks=[]), recorder(tmp_path / "o"))
    with pytest.raises(SpeechError):
        await spk.speak("Hi.", "s", lambda: None)


async def test_warm_loads_once_and_synthesises_off_the_record(tmp_path: Path) -> None:
    synth = FakeSynth()
    loads: list[int] = []

    def load() -> FakeSynth:
        loads.append(1)
        return synth

    spk = PipeWireSpeaker(load, player=lambda sink, rate: recorder(tmp_path / "o"))
    await spk.warm()
    await spk.warm()
    assert loads == [1]
    assert len(synth.texts) == 1, "one throwaway synthesis, played nowhere"
    assert not (tmp_path / "o").exists()


async def test_a_failed_load_is_a_speech_error_and_is_tried_again() -> None:
    attempts: list[int] = []

    def load() -> FakeSynth:
        attempts.append(1)
        raise SpeechError("no voice")

    spk = PipeWireSpeaker(load)
    for _ in range(2):
        with pytest.raises(SpeechError):
            await spk.warm()
    assert attempts == [1, 1]


def test_load_piper_without_the_voice_is_a_speech_error(tmp_path: Path) -> None:
    with pytest.raises(SpeechError):
        speech.load_piper(tmp_path / "missing.onnx", tmp_path / "missing.onnx.json")


# --- speaking while the route stays closed ---------------------------------------------------


class Routes:
    """Scripted ``read_route``: each call returns the next, then the last forever."""

    def __init__(self, *seq: tuple[Route, Sink | None]) -> None:
        self.seq = list(seq)
        self.calls = 0

    async def __call__(self) -> tuple[Route, Sink | None]:
        self.calls += 1
        return self.seq.pop(0) if len(self.seq) > 1 else self.seq[0]


class SlowSpeaker:
    def __init__(self, seconds: float = 30.0, fail: bool = False) -> None:
        self.seconds = seconds
        self.fail = fail
        self.stopped = False

    async def warm(self) -> None:
        pass

    async def speak(self, text: str, sink: str, on_first_audio: speech.FirstAudio) -> None:
        if self.fail:
            raise SpeechError("broke")
        on_first_audio()
        try:
            await asyncio.sleep(self.seconds)
        except asyncio.CancelledError:
            self.stopped = True
            raise


async def test_a_reply_that_finishes_is_done() -> None:
    first: list[int] = []
    played = await speak_guarded(
        SlowSpeaker(0.05),
        "Hi.",
        HEADPHONES,
        Routes((Route.HEADPHONES, HEADPHONES)),
        lambda: first.append(1),
        poll=0.01,
    )
    assert played is Played.DONE and first == [1]


@pytest.mark.parametrize(
    "after",
    [(Route.SPEAKERS, SPEAKERS), (Route.UNKNOWN, None), (Route.BLUETOOTH, OTHER)],
    ids=["unplugged to speakers", "unreadable", "another closed sink"],
)
async def test_a_route_change_while_speaking_stops_it_at_once(
    after: tuple[Route, Sink | None],
) -> None:
    spk = SlowSpeaker()
    routes = Routes((Route.HEADPHONES, HEADPHONES), after)
    began = time.monotonic()
    played = await speak_guarded(spk, "Hi.", HEADPHONES, routes, lambda: None, poll=0.02)
    assert played is Played.INTERRUPTED and spk.stopped
    assert time.monotonic() - began < 0.5


async def test_a_speech_failure_is_failed() -> None:
    played = await speak_guarded(
        SlowSpeaker(fail=True),
        "Hi.",
        HEADPHONES,
        Routes((Route.HEADPHONES, HEADPHONES)),
        lambda: None,
        poll=0.01,
    )
    assert played is Played.FAILED


async def test_cancelling_the_guard_stops_the_speech() -> None:
    spk = SlowSpeaker()
    task = asyncio.create_task(
        speak_guarded(
            spk, "Hi.", HEADPHONES, Routes((Route.HEADPHONES, HEADPHONES)), lambda: None, poll=0.01
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert spk.stopped


# --- the real voice ----------------------------------------------------------------------


@pytest.mark.model
def test_piper_speaks_with_the_installed_voice() -> None:
    from jarvis_desktop.stt import models

    pytest.importorskip("piper")
    root = models.models_dir()
    if not all(models.is_installed(s, root) for s in models.PIPER_VOICE):
        pytest.skip("the Piper voice is not installed (jarvis-voice models install)")
    synth = speech.load_piper(*speech.voice_paths(root))
    audio = b"".join(synth.chunks("It is three in the afternoon."))
    assert synth.sample_rate == 22050
    assert len(audio) / 2 / synth.sample_rate > 1.0


async def test_the_default_speaker_uses_the_installed_voice(tmp_path: Path) -> None:
    env = {"XDG_DATA_HOME": str(tmp_path), "HOME": "/nowhere"}
    spk = speech.default_speaker(env)
    with pytest.raises(SpeechError, match="models install"):
        await spk.warm()
