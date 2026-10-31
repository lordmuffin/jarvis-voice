import io
import json
import wave

import httpx
import pytest

from jarvis_live.stt.backend import WhisperBackend, pcm_to_wav
from jarvis_live.stt.guards import (
    is_duplicate_of,
    is_hallucination,
    normalize,
    overlap_ratio,
    rms_level,
)
from jarvis_live.stt.tiers import STTUnavailable, Tier, TierPool, parse_tiers
from tests.audio import silence, tone

SPEC = """
# fastest first
gpu   http://gpu.local:8000/v1   large-v3
cpu   http://cpu.local:9000/v1/  small.en

"""


def test_parse_tiers() -> None:
    assert parse_tiers(SPEC) == [
        Tier("gpu", "http://gpu.local:8000/v1", "large-v3"),
        Tier("cpu", "http://cpu.local:9000/v1", "small.en"),
    ]
    assert parse_tiers("") == []


@pytest.mark.parametrize(
    "bad",
    [
        "gpu http://x/v1",  # missing model
        "gpu http://x/v1 m extra",
        "gpu ftp://x/v1 m",
        "a http://x/v1 m\na http://y/v1 m",  # duplicate name
    ],
)
def test_parse_tiers_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_tiers(bad)


class FakeWhisper:
    """httpx.MockTransport handler with per-host behaviour for each endpoint."""

    def __init__(self, **hosts: dict[str, int]) -> None:
        self.hosts = hosts  # host -> {"models": status, "health": status, "transcribe": status}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host.split(".")[0]
        behaviour = self.hosts[host]
        path = request.url.path
        if path.endswith("/models"):
            return httpx.Response(behaviour.get("models", 200), json={"data": []})
        if path.endswith("/health"):
            return httpx.Response(behaviour.get("health", 404))
        if path.endswith("/audio/transcriptions"):
            status = behaviour.get("transcribe", 200)
            if status == -1:
                raise httpx.ConnectError("boom")
            if status != 200:
                return httpx.Response(status)
            return httpx.Response(200, json={"text": f" from {host} "})
        return httpx.Response(404)

    def posts(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]


def pool(fake: FakeWhisper) -> TierPool:
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return TierPool(parse_tiers(SPEC), client)


async def test_uses_first_healthy_tier() -> None:
    fake = FakeWhisper(gpu={}, cpu={})
    p = pool(fake)
    assert await p.transcribe(b"wav", "") == ("from gpu", "gpu")
    assert p.current_tier() == "gpu"
    assert [r.url.host for r in fake.posts()] == ["gpu.local"]


async def test_unhealthy_tier_is_skipped() -> None:
    fake = FakeWhisper(gpu={"models": 503, "health": 503}, cpu={})
    p = pool(fake)
    assert await p.transcribe(b"wav", "") == ("from cpu", "cpu")
    assert [r.url.host for r in fake.posts()] == ["cpu.local"]


async def test_2xx_health_counts_as_healthy() -> None:
    fake = FakeWhisper(gpu={"models": 404, "health": 200}, cpu={})
    p = pool(fake)
    await p.probe_all()
    assert p.current_tier() == "gpu"


@pytest.mark.parametrize("failure", [500, 429, -1])
async def test_request_error_falls_through_to_next_tier(failure: int) -> None:
    fake = FakeWhisper(gpu={"transcribe": failure}, cpu={})
    p = pool(fake)
    assert await p.transcribe(b"wav", "") == ("from cpu", "cpu")
    assert [r.url.host for r in fake.posts()] == ["gpu.local", "cpu.local"]
    # the failing tier is marked unhealthy right away, not only at the next probe
    assert p.current_tier() == "cpu"


async def test_all_tiers_failing_raises() -> None:
    fake = FakeWhisper(gpu={"transcribe": 500}, cpu={"transcribe": -1})
    with pytest.raises(STTUnavailable):
        await pool(fake).transcribe(b"wav", "")


async def test_probe_recovers_tier() -> None:
    fake = FakeWhisper(gpu={"transcribe": 500}, cpu={})
    p = pool(fake)
    await p.transcribe(b"wav", "")
    assert p.current_tier() == "cpu"
    fake.hosts["gpu"] = {}
    await p.probe_all()
    assert p.current_tier() == "gpu"


async def test_all_unhealthy_still_tries_every_tier() -> None:
    fake = FakeWhisper(gpu={"models": 500}, cpu={"models": 500})
    p = pool(fake)
    assert await p.transcribe(b"wav", "") == ("from gpu", "gpu")


async def test_request_shape() -> None:
    fake = FakeWhisper(gpu={}, cpu={})
    backend = WhisperBackend(pool(fake))
    prompt = "x" * 50
    result = await backend.transcribe(tone(0.5), prompt)
    assert (result.text, result.tier) == ("from gpu", "gpu")
    (req,) = fake.posts()
    assert str(req.url) == "http://gpu.local:8000/v1/audio/transcriptions"
    body = req.read()
    assert req.headers["content-type"].startswith("multipart/form-data")
    for needle in (
        b'name="model"\r\n\r\nlarge-v3',
        b'name="language"\r\n\r\nen',
        f"{prompt}".encode(),
    ):
        assert needle in body
    wav_start = body.index(b"RIFF")
    with wave.open(io.BytesIO(body[wav_start : body.index(b"\r\n--", wav_start)]), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16_000)
        assert w.getnframes() == 8000


def test_pcm_to_wav_roundtrip() -> None:
    pcm = tone(0.1)
    with wave.open(io.BytesIO(pcm_to_wav(pcm)), "rb") as w:
        assert w.readframes(w.getnframes()) == pcm


async def test_empty_prompt_is_omitted() -> None:
    fake = FakeWhisper(gpu={}, cpu={})
    await WhisperBackend(pool(fake)).transcribe(tone(0.5), "")
    assert b'name="prompt"' not in fake.posts()[0].read()


async def test_malformed_response_is_a_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200)
        if request.url.host == "gpu.local":
            return httpx.Response(200, content=json.dumps({"nope": 1}))
        return httpx.Response(200, json={"text": "ok"})

    p = TierPool(parse_tiers(SPEC), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await p.transcribe(b"wav", "") == ("ok", "cpu")


# --- guards ----------------------------------------------------------------------------------

PHRASES = ["thank you for watching", "subtitles by"]


def test_hallucination_dropped_only_when_quiet() -> None:
    quiet, loud = rms_level(silence(0.5)), rms_level(tone(0.5))
    assert is_hallucination("Thank you for watching!", quiet, PHRASES, 0.01)
    assert is_hallucination("...Subtitles by the Amara.org community", quiet, PHRASES, 0.01)
    assert not is_hallucination("Thank you for watching!", loud, PHRASES, 0.01)
    assert not is_hallucination("please send the report", quiet, PHRASES, 0.01)


def test_rms_level_is_normalized() -> None:
    assert rms_level(b"") == 0.0
    assert rms_level(silence(0.1)) == 0.0
    assert rms_level(tone(0.5, amp=0.5)) == pytest.approx(0.5 / 2**0.5, rel=0.01)


def test_normalize() -> None:
    assert normalize("  Hello,   WORLD! ") == "hello world"


class Span:
    def __init__(self, start_ms: int, end_ms: int, text: str) -> None:
        self.start_ms, self.end_ms, self.text = start_ms, end_ms, text


def test_overlap_ratio_is_relative_to_first_interval() -> None:
    assert overlap_ratio(0, 1000, 500, 5000) == 0.5
    assert overlap_ratio(0, 1000, 2000, 3000) == 0.0
    assert overlap_ratio(0, 0, 0, 10) == 0.0


def test_duplicate_guard() -> None:
    mic = Span(1000, 3000, "Let's move the launch to Friday")
    echo = Span(1100, 3100, "let's move the launch to friday.")
    check = lambda *s: is_duplicate_of(mic, s, 0.5, 0.8)  # noqa: E731
    assert check(echo)
    assert not check(Span(1100, 3100, "completely different sentence here"))  # text differs
    assert not check(Span(2500, 4500, "Let's move the launch to Friday"))  # overlap 25%
    assert not check(Span(2000, 4000, "Let's move the launch to Friday"))  # exactly 50%: not more
    assert check(Span(1900, 4000, "Let's move the launch to Friday"))  # 55%
