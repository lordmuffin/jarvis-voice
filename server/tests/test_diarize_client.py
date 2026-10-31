import wave
from io import BytesIO
from pathlib import Path

import httpx
import pytest

from jarvis_live.diarize.align import Turn
from jarvis_live.diarize.client import DiarizeUnavailable, HttpDiarizer

PCM = bytes(range(256)) * 250  # 64 000 bytes = 2 s at 16 kHz mono PCM16


@pytest.fixture
def pcm(tmp_path: Path) -> Path:
    p = tmp_path / "mic.pcm"
    p.write_bytes(PCM)
    return p


def _diarizer(handler: httpx.MockTransport) -> HttpDiarizer:
    return HttpDiarizer("http://diarize:8000/", httpx.AsyncClient(transport=handler), timeout_s=5)


async def test_posts_the_recording_as_wav_and_parses_turns(pcm: Path) -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = req.content
        return httpx.Response(
            200,
            json={
                "turns": [
                    {"start_ms": 0, "end_ms": 900, "speaker": "SPEAKER_00"},
                    {"start_ms": 900, "end_ms": 2000, "speaker": "SPEAKER_01"},
                ]
            },
        )

    result = await _diarizer(httpx.MockTransport(handler)).diarize(pcm)

    assert result.turns == [Turn(0, 900, "SPEAKER_00"), Turn(900, 2000, "SPEAKER_01")]
    assert result.embeddings == {} and result.model_id is None  # a sidecar may omit them
    assert seen["url"] == "http://diarize:8000/diarize"
    with wave.open(BytesIO(seen["body"]), "rb") as w:  # type: ignore[arg-type]
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16_000)
        assert w.readframes(w.getnframes()) == PCM


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"turns": "x"}),
        httpx.Response(200, json={"turns": [{"start_ms": 5, "end_ms": 1}]}),
    ],
    ids=["http_error", "bad_json", "bad_shape", "missing_speaker"],
)
async def test_any_failure_is_diarize_unavailable(pcm: Path, response: httpx.Response) -> None:
    d = _diarizer(httpx.MockTransport(lambda req: response))
    with pytest.raises(DiarizeUnavailable):
        await d.diarize(pcm)


async def test_connection_error_is_diarize_unavailable(pcm: Path) -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(DiarizeUnavailable):
        await _diarizer(httpx.MockTransport(boom)).diarize(pcm)


async def test_voice_embeddings_come_back_per_speaker(pcm: Path) -> None:
    body = {
        "turns": [{"start_ms": 0, "end_ms": 900, "speaker": "SPEAKER_00"}],
        "embeddings": {"SPEAKER_00": [0.5, -0.5, 1]},
        "model_id": "wespeaker-resnet34",
    }
    d = _diarizer(httpx.MockTransport(lambda req: httpx.Response(200, json=body)))
    result = await d.diarize(pcm)
    assert result.embeddings == {"SPEAKER_00": [0.5, -0.5, 1.0]}
    assert result.model_id == "wespeaker-resnet34"


@pytest.mark.parametrize(
    "extra",
    [{"embeddings": [1, 2]}, {"embeddings": {"A": "x"}}, {"embeddings": {"A": [1, "x"]}}],
    ids=["not_a_map", "not_a_vector", "not_numbers"],
)
async def test_malformed_embeddings_are_diarize_unavailable(
    pcm: Path, extra: dict[str, object]
) -> None:
    body = {"turns": [], **extra}
    d = _diarizer(httpx.MockTransport(lambda req: httpx.Response(200, json=body)))
    with pytest.raises(DiarizeUnavailable):
        await d.diarize(pcm)


async def test_embed_posts_the_segment_as_wav_and_returns_the_print() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(req.url), req.content
        return httpx.Response(200, json={"embedding": [0.25, 0.5, 1], "model_id": "wespeaker"})

    got = await _diarizer(httpx.MockTransport(handler)).embed(PCM)

    assert (got.vector, got.model_id) == ([0.25, 0.5, 1.0], "wespeaker")
    assert seen["url"] == "http://diarize:8000/embed"
    with wave.open(BytesIO(seen["body"]), "rb") as w:  # type: ignore[arg-type]
        assert w.readframes(w.getnframes()) == PCM


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, text="nope"),
        httpx.Response(200, json={"embedding": "x", "model_id": "m"}),
        httpx.Response(200, json={"embedding": [1.0]}),
        httpx.Response(200, json={"embedding": [], "model_id": "m"}),
    ],
    ids=["http_error", "bad_json", "bad_vector", "no_model", "empty"],
)
async def test_embed_failures_are_diarize_unavailable(response: httpx.Response) -> None:
    with pytest.raises(DiarizeUnavailable):
        await _diarizer(httpx.MockTransport(lambda req: response)).embed(PCM)
