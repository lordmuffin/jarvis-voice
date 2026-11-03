"""On-device speech-to-text engines behind one narrow interface.

An engine turns 16 kHz mono PCM16 into text. It never touches the network, the capture outbox or
the session state: dictation stays on this machine (plan section 2.5). The chosen engine is
Parakeet-TDT-0.6B-v2 int8 on CPU through sherpa-onnx (plan section 2.2); another engine
(whisper.cpp on Vulkan, say) can sit behind the same ``Engine`` without touching the controller."""

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
