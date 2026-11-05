"""Speaks an ask's reply: Piper on the CPU, into a PipeWire stream pinned to an approved sink.

docs/plans/voice-push-to-talk.md. The voice loads once, when a capture session starts
(``warm``), because a cold load costs about a second. Piper synthesises in this process, a
sentence at a time, and each sentence goes straight into a ``pw-cat`` playback stream the app
spawns and owns, so the first audio does not wait for the whole reply.

The stream targets the sink the route gate approved (route.py) and asks WirePlumber never to
move it: if the headphones go, the stream is not carried to the speakers. ``speak_guarded``
also watches the default sink while speaking and stops at once if it changes or stops being
closed. Piper is the optional ``speech`` extra (``uv sync --extra speech``) and its voice comes
from ``jarvis-voice models install``; without either, speech fails and the reply is text only."""

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable, Iterator, Mapping
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from jarvis_desktop.route import Route, Sink
from jarvis_desktop.stt import models

FirstAudio = Callable[[], None]
ReadRoute = Callable[[], Awaitable[tuple[Route, Sink | None]]]
#: How often the default sink is read while speaking. ``pw-dump`` takes about 15 ms.
POLL_SECONDS = 0.1
_WARM_TEXT = "Ready."


class SpeechError(Exception):
    pass


class Synth(Protocol):
    sample_rate: int

    def chunks(self, text: str) -> Iterator[bytes]:
        """Mono s16le PCM, a sentence at a time."""
        ...


class Speaker(Protocol):
    async def warm(self) -> None:
        """Load the voice now, so the first reply doesn't wait for it. Raises ``SpeechError``."""
        ...

    async def speak(self, text: str, sink: str, on_first_audio: FirstAudio) -> None:
        """Returns once played out; raises ``SpeechError``. Cancelling stops it at once."""
        ...


class Played(Enum):
    DONE = "done"
    INTERRUPTED = "interrupted"  # the route changed while speaking
    FAILED = "failed"


def voice_paths(root: Path) -> tuple[Path, Path]:
    """The installed Piper voice and its config."""
    voice, config = models.PIPER_VOICE
    return root / voice.name / voice.files[0], root / config.name / config.files[0]


class _PiperSynth:
    def __init__(self, voice: Any) -> None:
        self._voice = voice
        self.sample_rate: int = int(voice.config.sample_rate)

    def chunks(self, text: str) -> Iterator[bytes]:
        for chunk in self._voice.synthesize(text):
            yield bytes(chunk.audio_int16_bytes)


def load_piper(model: Path, config: Path) -> Synth:
    if not model.is_file() or not config.is_file():
        raise SpeechError("the Piper voice is not installed: run `jarvis-voice models install`")
    try:
        from piper import PiperVoice
    except ImportError as e:
        raise SpeechError("Piper is not installed: run `uv sync --extra speech`") from e
    try:
        return _PiperSynth(PiperVoice.load(model, config_path=config))
    except Exception as e:  # onnxruntime and Piper raise their own types
        raise SpeechError(f"could not load the Piper voice: {e}") from e


def default_speaker(env: Mapping[str, str] = os.environ) -> "PipeWireSpeaker":
    """Piper with the voice ``jarvis-voice models install`` put in the models directory."""
    model, config = voice_paths(models.models_dir(env))
    return PipeWireSpeaker(lambda: load_piper(model, config))


def player_command(sink: str, rate: int) -> list[str]:
    return [
        "pw-cat",
        "--playback",
        "--raw",
        "--target",
        sink,
        "--rate",
        str(rate),
        "--channels",
        "1",
        "--format",
        "s16",
        "--media-role",
        "Assistant",
        "--properties",
        "{ node.dont-reconnect = true }",
        "-",
    ]


class PipeWireSpeaker:
    def __init__(
        self,
        load: Callable[[], Synth],
        *,
        player: Callable[[str, int], list[str]] = player_command,
    ) -> None:
        self._load = load
        self._player = player
        self._synth: Synth | None = None
        self._lock = asyncio.Lock()
        #: The player while it runs.
        self.playing: asyncio.subprocess.Process | None = None

    async def warm(self) -> None:
        await self._ready()

    async def _ready(self) -> Synth:
        async with self._lock:
            if self._synth is None:
                synth = await asyncio.to_thread(self._load)
                # One throwaway synthesis, played nowhere: the first real one runs warm.
                await asyncio.to_thread(_drain, synth, _WARM_TEXT)
                self._synth = synth
            return self._synth

    async def speak(self, text: str, sink: str, on_first_audio: FirstAudio) -> None:
        synth = await self._ready()
        chunks = synth.chunks(text)
        first = await _next(chunks)
        if first is None:
            raise SpeechError("Piper made no audio")
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._player(sink, synth.sample_rate),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as e:
            raise SpeechError(f"cannot start the player: {e}") from e
        self.playing = proc
        assert proc.stdin is not None
        try:
            chunk: bytes | None = first
            while chunk is not None:
                proc.stdin.write(chunk)
                await proc.stdin.drain()
                if chunk is first:
                    on_first_audio()
                chunk = await _next(chunks)
            proc.stdin.close()
            code = await proc.wait()
        except (BrokenPipeError, ConnectionResetError) as e:
            await _stop(proc)
            raise SpeechError("the player stopped") from e
        except BaseException:
            await _stop(proc)  # cancelled: the route changed, or another ask began
            raise
        finally:
            self.playing = None
        if code != 0:
            raise SpeechError(f"the player exited with {code}")


async def _next(chunks: Iterator[bytes]) -> bytes | None:
    try:
        return await asyncio.to_thread(next, chunks, None)
    except Exception as e:  # Piper's own failures
        raise SpeechError(f"Piper failed: {e}") from e


def _drain(synth: Synth, text: str) -> None:
    try:
        for _ in synth.chunks(text):
            pass
    except Exception as e:  # Piper's own failures
        raise SpeechError(f"Piper failed: {e}") from e


async def _stop(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    await proc.wait()


async def speak_guarded(
    speaker: Speaker,
    text: str,
    sink: Sink,
    read_route: ReadRoute,
    on_first_audio: FirstAudio,
    *,
    poll: float = POLL_SECONDS,
) -> Played:
    """Speaks ``text`` into ``sink`` for as long as it stays the default and closed."""
    task = asyncio.create_task(speaker.speak(text, sink.name, on_first_audio))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=poll)
            if done:
                try:
                    task.result()
                except SpeechError:
                    return Played.FAILED
                return Played.DONE
            route, now = await read_route()
            if not route.closed or now is None or now.name != sink.name:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                return Played.INTERRUPTED
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
