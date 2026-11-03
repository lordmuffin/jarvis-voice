"""The Engine contract and the Parakeet engine's PCM handling."""

import struct
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from jarvis_desktop.stt import models
from jarvis_desktop.stt.engine import Engine, EngineError, ParakeetEngine


class FakeStream:
    def __init__(self) -> None:
        self.samples: Any = None
        self.rate = 0

        class Result:
            text = " Hello there. "

        self.result = Result()

    def accept_waveform(self, rate: int, samples: Any) -> None:
        self.rate, self.samples = rate, samples


class FakeRecognizer:
    def __init__(self) -> None:
        self.streams: list[FakeStream] = []

    def create_stream(self) -> FakeStream:
        self.streams.append(FakeStream())
        return self.streams[-1]

    def decode_stream(self, stream: FakeStream) -> None:
        pass


def engine_with_fake() -> tuple[ParakeetEngine, FakeRecognizer]:
    rec = FakeRecognizer()
    return ParakeetEngine(Path("/unused"), recognizer=rec), rec


def test_parakeet_is_an_engine() -> None:
    engine, _ = engine_with_fake()
    assert isinstance(engine, Engine)
    assert engine.name == "parakeet-tdt-0.6b-v2-int8"


def test_pcm16_becomes_float_samples_at_16k() -> None:
    engine, rec = engine_with_fake()
    pcm = struct.pack("<3h", 0, 16384, -32768)
    assert engine.transcribe(pcm) == "Hello there."
    stream = rec.streams[0]
    assert stream.rate == 16000
    assert stream.samples.dtype == np.float32
    assert stream.samples.tolist() == [0.0, 0.5, -1.0]


def test_no_audio_is_no_text_without_decoding() -> None:
    engine, rec = engine_with_fake()
    assert engine.transcribe(b"") == ""
    assert rec.streams == []


def test_a_torn_sample_is_refused() -> None:
    engine, _ = engine_with_fake()
    with pytest.raises(EngineError, match="whole 16-bit samples"):
        engine.transcribe(b"\x00\x01\x02")


def test_a_missing_model_says_so(tmp_path: Path) -> None:
    with pytest.raises(EngineError, match="not installed"):
        ParakeetEngine(tmp_path / "parakeet")


MODEL_DIR = models.models_dir() / models.PARAKEET_TDT_V2.name
needs_model = pytest.mark.skipif(
    not models.is_installed(models.PARAKEET_TDT_V2, models.models_dir()),
    reason="needs the installed Parakeet model",
)


@pytest.fixture(scope="module")
def real_engine() -> ParakeetEngine:
    return ParakeetEngine(MODEL_DIR)


@pytest.mark.model
@needs_model
def test_real_model_hears_nothing_in_silence(real_engine: ParakeetEngine) -> None:
    assert real_engine.transcribe(bytes(16_000 * 2 * 3)) == ""


@pytest.mark.model
@needs_model
def test_real_model_decodes_ten_seconds_inside_the_release_budget(
    real_engine: ParakeetEngine,
) -> None:
    """Plan section 2.2: the whole release-to-paste budget is about 1 s; decode gets half."""
    rng = np.random.default_rng(0)
    noise = (rng.normal(0, 300, 16_000 * 10)).astype("<i2").tobytes()
    real_engine.transcribe(noise)  # warm-up
    start = time.perf_counter()
    real_engine.transcribe(noise)
    assert time.perf_counter() - start < 0.5
