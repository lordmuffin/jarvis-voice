"""On-device speech-to-text engines behind one narrow interface.

An engine turns 16 kHz mono PCM16 into text. It never touches the network, the capture outbox or
the session state: dictation stays on this machine (plan section 2.5). The chosen engine is
Whisper large-v3-turbo through whisper.cpp on Vulkan (plan section 2.2, chosen on Andrew's read
set), with Parakeet-TDT-0.6B-v2 int8 on CPU through sherpa-onnx as the automatic fallback."""

import os
import shutil
import subprocess
import tempfile
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

from jarvis_desktop.stt import models

SAMPLE_RATE = 16_000


class EngineError(Exception):
    pass


@runtime_checkable
class Engine(Protocol):
    name: str

    def transcribe(self, pcm: bytes) -> str:
        """Text for one utterance of 16 kHz mono PCM16 (little-endian)."""
        ...


class ParakeetEngine:
    name = models.PARAKEET_TDT_V2.name

    def __init__(self, model_dir: Path, *, threads: int = 8, recognizer: Any = None) -> None:
        """Loads the model once (about 2 s); keep the engine for the life of the app.
        ``recognizer`` replaces the sherpa-onnx recognizer in tests."""
        if recognizer is None:
            recognizer = self._load(model_dir, threads)
        self._recognizer = recognizer

    @staticmethod
    def _load(model_dir: Path, threads: int) -> Any:
        files = models.PARAKEET_TDT_V2.files
        missing = [f for f in files if not (model_dir / f).is_file()]
        if missing:
            raise EngineError(
                f"the speech model is not installed in {model_dir} (missing {', '.join(missing)})"
            )
        import sherpa_onnx  # imported here so tests and the CLI start without loading it

        return sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(model_dir / "encoder.int8.onnx"),
            decoder=str(model_dir / "decoder.int8.onnx"),
            joiner=str(model_dir / "joiner.int8.onnx"),
            tokens=str(model_dir / "tokens.txt"),
            num_threads=threads,
            model_type="nemo_transducer",
        )

    def transcribe(self, pcm: bytes) -> str:
        if len(pcm) % 2:
            raise EngineError("audio must be whole 16-bit samples")
        if not pcm:
            return ""
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        stream = self._recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        self._recognizer.decode_stream(stream)
        return str(stream.result.text).strip()


class WhisperCppEngine:
    """Whisper large-v3-turbo through whisper.cpp built with Vulkan (the default engine).

    Each utterance is written to a private temporary WAV (a 0700 directory under
    ``$XDG_RUNTIME_DIR``, a 0600 file, removed afterwards) and decoded by ``whisper-cli``, which
    loads the model each time: about 200 ms of the 430 ms a 10 s utterance takes on the RX 9070 XT,
    still inside the release budget. No socket of any kind is opened."""

    name = models.WHISPER_TURBO_Q5.name

    def __init__(
        self, model: Path, *, cli: Path | None = None, threads: int = 8, timeout: float = 30.0
    ) -> None:
        if not model.is_file():
            raise EngineError(
                f"the Whisper model is not installed ({model}): run `jarvis-voice models install`"
            )
        found = cli or _whisper_cli()
        if found is None:
            raise EngineError(
                "whisper-cli not found: install whisper.cpp built with Vulkan, or point "
                "JARVIS_WHISPER_CLI at its whisper-cli"
            )
        self.cli = found
        self.model = model
        self.threads = threads
        self.timeout = timeout

    def transcribe(self, pcm: bytes) -> str:
        if len(pcm) % 2:
            raise EngineError("audio must be whole 16-bit samples")
        if not pcm:
            return ""
        base = os.environ.get("XDG_RUNTIME_DIR") or None
        with tempfile.TemporaryDirectory(prefix="jarvis-voice-stt-", dir=base) as d:
            wav = Path(d) / "utterance.wav"
            fd = os.open(wav, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh, wave.open(fh, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(pcm)
            cmd = [str(self.cli), "-m", str(self.model), "-f", str(wav)]
            cmd += ["-l", "en", "-t", str(self.threads), "-nt", "-np"]
            try:
                done = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
            except subprocess.TimeoutExpired as e:
                raise EngineError(f"whisper-cli timed out after {self.timeout:g} s") from e
            except OSError as e:
                raise EngineError(f"whisper-cli could not run: {e}") from e
        if done.returncode != 0:
            tail = done.stderr.strip().splitlines()[-3:]
            raise EngineError(f"whisper-cli failed ({done.returncode}): {' | '.join(tail)}")
        return " ".join(done.stdout.split())


def _whisper_cli() -> Path | None:
    if env := os.environ.get("JARVIS_WHISPER_CLI"):
        return Path(env)
    found = shutil.which("whisper-cli")
    return Path(found) if found else None


class FallbackEngine:
    """The default engine with an automatic fallback: if the primary fails (a lost GPU, a broken
    driver, a missing binary at run time), the fallback answers, and the primary rests for
    ``retry_after`` seconds so a dead GPU doesn't cost a failed attempt on every dictation."""

    def __init__(
        self,
        primary: Engine,
        fallback: Engine,
        *,
        retry_after: float = 300.0,
        on_fallback: Callable[[str], None] = lambda _: None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = primary.name
        self._retry_after = retry_after
        self._on_fallback = on_fallback
        self._clock = clock
        self._resting_until: float | None = None

    def transcribe(self, pcm: bytes) -> str:
        if self._resting_until is None or self._clock() >= self._resting_until:
            try:
                text = self.primary.transcribe(pcm)
            except EngineError as e:
                self._resting_until = self._clock() + self._retry_after
                self._on_fallback(str(e))
            else:
                self._resting_until = None
                return text
        return self.fallback.transcribe(pcm)
