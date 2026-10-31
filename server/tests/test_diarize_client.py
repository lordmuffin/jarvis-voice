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

    turns = await _diarizer(httpx.MockTransport(handler)).diarize(pcm)

    assert turns == [Turn(0, 900, "SPEAKER_00"), Turn(900, 2000, "SPEAKER_01")]
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
