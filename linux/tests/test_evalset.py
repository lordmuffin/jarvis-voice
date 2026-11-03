"""Pulling an STT evaluation set (your own mic audio + the server's final text) from a server."""

import array
import functools
import io
import json
import struct
import wave
from pathlib import Path
from typing import Any

import httpx
import pytest

from jarvis_desktop import evalset
from jarvis_desktop.evalset import Clip, plan_clips, pull, wanted

TOKEN = "dev-token-not-a-secret-0123456789"
BYTES_PER_MS = 32


def seg(
    start: int, end: int, text: str, channel: str = "mic", speaker: str = "me"
) -> dict[str, Any]:
    return {
        "id": f"s{start}",
        "channel": channel,
        "speaker": speaker,
        "start_ms": start,
        "end_ms": end,
        "text": text,
        "stt_tier": "whisper-large",
    }


def session(sid: str, **over: Any) -> dict[str, Any]:
    base = {
        "id": sid,
        "title": None,
        "mode": "solo",
        "status": "done",
        "channels": ["mic"],
        "restricted": False,
        "local_only": False,
    }
    return {**base, **over}


@functools.cache
def wav_bytes(duration_ms: int) -> bytes:
    """A server-style WAV: 44-byte header, 16 kHz mono PCM16, samples counting up."""
    pcm = array.array("h", (i % 30000 for i in range(duration_ms * 16))).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()


class FakeServer:
    """Just enough of the Jarvis Live REST API: list, detail, ranged WAV."""

    def __init__(self, sessions: list[dict[str, Any]], segments: dict[str, list[dict[str, Any]]]):
        self.sessions = sessions
        self.segments = segments
        self.audio = {s["id"]: wav_bytes(60_000) for s in sessions}
        self.requests: list[httpx.Request] = []
        self.audio_bytes_served = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"detail": "Invalid or revoked token."})
        path = request.url.path
        if path == "/v1/sessions":
            limit = int(request.url.params.get("limit", 20))
            offset = int(request.url.params.get("offset", 0))
            items = self.sessions[offset : offset + limit]
            return httpx.Response(
                200,
                json={
                    "items": items,
                    "total": len(self.sessions),
                    "limit": limit,
                    "offset": offset,
                },
            )
        parts = path.split("/")
        sid = parts[3]
        if len(parts) == 4:
            s = next(x for x in self.sessions if x["id"] == sid)
            return httpx.Response(200, json={**s, "segments": self.segments.get(sid, [])})
        data = self.audio[sid]
        start, end = 0, len(data) - 1
        if rng := request.headers.get("range"):
            a, b = rng.removeprefix("bytes=").split("-")
            start, end = int(a), min(int(b), len(data) - 1)
        body = data[start : end + 1]
        self.audio_bytes_served += len(body)
        return httpx.Response(206 if rng else 200, content=body)


def client(server: FakeServer, token: str = TOKEN) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://live.example",
        transport=httpx.MockTransport(server.handler),
        headers={"Authorization": f"Bearer {token}"},
    )


def manifest(out: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (out / "manifest.jsonl").read_text().splitlines()]


# --- which sessions --------------------------------------------------------------------------


def test_wants_finished_unrestricted_solo_sessions_with_mic_audio() -> None:
    assert wanted(session("a"), modes={"solo"}, include_restricted=False)
    assert not wanted(session("a", status="live"), modes={"solo"}, include_restricted=False)
    assert not wanted(session("a", restricted=True), modes={"solo"}, include_restricted=False)
    assert wanted(session("a", restricted=True), modes={"solo"}, include_restricted=True)
    assert not wanted(session("a", mode="meeting"), modes={"solo"}, include_restricted=False)
    assert wanted(session("a", mode="meeting"), modes={"solo", "meeting"}, include_restricted=False)
    assert not wanted(session("a", channels=["system"]), modes={"solo"}, include_restricted=False)
    assert not wanted(session("a", local_only=True), modes={"solo"}, include_restricted=False)


# --- which clips -----------------------------------------------------------------------------


def test_clips_are_my_mic_segments_merged_across_short_pauses() -> None:
    detail = session(
        "abc",
        segments=[
            seg(0, 3_000, "Okay, quick update."),
            seg(3_300, 7_000, "I moved the deploy."),  # 300 ms gap: same utterance
            seg(9_000, 13_000, "Sarah signs off first."),  # 2 s gap: a new one
            seg(9_500, 12_000, "Other person.", channel="system", speaker="them"),
        ],
    )
    clips = plan_clips(detail, min_ms=2_000, max_ms=20_000, max_gap_ms=600)
    assert clips == [
        Clip("abc", 0, 7_000, "Okay, quick update. I moved the deploy.", "whisper-large"),
        Clip("abc", 9_000, 13_000, "Sarah signs off first.", "whisper-large"),
    ]


def test_clips_respect_the_length_bounds() -> None:
    detail = session(
        "abc",
        segments=[
            seg(0, 1_000, "Too short."),
            seg(5_000, 15_000, "Ten seconds."),
            seg(15_100, 26_000, "Would make it too long."),
        ],
    )
    clips = plan_clips(detail, min_ms=2_000, max_ms=20_000, max_gap_ms=600)
    assert [(c.start_ms, c.end_ms) for c in clips] == [(5_000, 15_000), (15_100, 26_000)]


def test_blank_segments_are_not_clips() -> None:
    detail = session("abc", segments=[seg(0, 5_000, "   ")])
    assert plan_clips(detail, min_ms=2_000, max_ms=20_000, max_gap_ms=600) == []


# --- pulling ---------------------------------------------------------------------------------


async def test_pull_writes_wavs_and_a_manifest(tmp_path: Path) -> None:
    server = FakeServer(
        [session("aaaa1111-0000-0000-0000-000000000000")],
        {"aaaa1111-0000-0000-0000-000000000000": [seg(1_000, 5_000, "Hello there.")]},
    )
    async with client(server) as c:
        summary = await pull(c, tmp_path, max_clips=10)
    assert summary.clips == 1
    [row] = manifest(tmp_path)
    assert row["reference"] == "Hello there."
    assert row["duration_ms"] == 4_000
    with wave.open(str(tmp_path / row["audio"]), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        assert w.getnframes() == 4_000 * 16
        frames = w.readframes(1)
    # The first sample is the server's sample at 1 s, so the byte range was the right one.
    assert struct.unpack("<h", frames)[0] == (1_000 * 16) % 30000


async def test_pull_fetches_only_the_clip_bytes(tmp_path: Path) -> None:
    sid = "aaaa1111-0000-0000-0000-000000000000"
    server = FakeServer([session(sid)], {sid: [seg(1_000, 5_000, "Hello there.")]})
    async with client(server) as c:
        await pull(c, tmp_path, max_clips=10)
    assert server.audio_bytes_served < 44 + 4_000 * BYTES_PER_MS + 4096


async def test_pull_skips_sessions_it_does_not_want(tmp_path: Path) -> None:
    sessions = [
        session("11111111-0000-0000-0000-000000000000", restricted=True),
        session("22222222-0000-0000-0000-000000000000", status="live"),
        session("33333333-0000-0000-0000-000000000000"),
    ]
    server = FakeServer(sessions, {s["id"]: [seg(0, 4_000, "Text.")] for s in sessions})
    async with client(server) as c:
        await pull(c, tmp_path, max_clips=10)
    assert {r["session"] for r in manifest(tmp_path)} == {"33333333-0000-0000-0000-000000000000"}
    detail_paths = {r.url.path for r in server.requests if r.url.path.count("/") == 3}
    assert detail_paths == {"/v1/sessions/33333333-0000-0000-0000-000000000000"}


async def test_pull_pages_through_the_session_list_and_stops_at_max_clips(tmp_path: Path) -> None:
    sessions = [session(f"{i:08d}-0000-0000-0000-000000000000") for i in range(150)]
    server = FakeServer(
        sessions, {s["id"]: [seg(0, 3_000, f"Clip {s['id'][:8]}.")] for s in sessions}
    )
    async with client(server) as c:
        summary = await pull(c, tmp_path, max_clips=120)
    assert summary.clips == 120
    assert len(manifest(tmp_path)) == 120
    offsets = [r.url.params.get("offset") for r in server.requests if r.url.path == "/v1/sessions"]
    assert offsets == ["0", "100"]


async def test_a_second_pull_adds_only_new_clips(tmp_path: Path) -> None:
    sid = "aaaa1111-0000-0000-0000-000000000000"
    server = FakeServer([session(sid)], {sid: [seg(0, 3_000, "One."), seg(10_000, 13_000, "Two.")]})
    async with client(server) as c:
        await pull(c, tmp_path, max_clips=1)
        summary = await pull(c, tmp_path, max_clips=10)
    assert summary.clips == 1 and summary.skipped == 1
    assert [r["reference"] for r in manifest(tmp_path)] == ["One.", "Two."]


async def test_the_token_is_never_written_to_disk(tmp_path: Path) -> None:
    sid = "aaaa1111-0000-0000-0000-000000000000"
    server = FakeServer([session(sid)], {sid: [seg(0, 3_000, "One.")]})
    async with client(server) as c:
        await pull(c, tmp_path, max_clips=10)
    for f in tmp_path.rglob("*"):
        if f.is_file():
            assert TOKEN.encode() not in f.read_bytes()


async def test_a_rejected_token_is_a_clear_error(tmp_path: Path) -> None:
    server = FakeServer([], {})
    async with client(server, token="wrong") as c:
        with pytest.raises(evalset.PullError, match="token"):
            await pull(c, tmp_path, max_clips=10)


async def test_a_wav_that_is_not_16k_mono_pcm16_is_refused(tmp_path: Path) -> None:
    sid = "aaaa1111-0000-0000-0000-000000000000"
    server = FakeServer([session(sid)], {sid: [seg(0, 3_000, "One.")]})
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\0" * 48000 * 4)
    server.audio[sid] = buf.getvalue()
    async with client(server) as c:
        with pytest.raises(evalset.PullError, match="16 kHz mono"):
            await pull(c, tmp_path, max_clips=10)


# --- where it writes -------------------------------------------------------------------------


def test_refuses_to_write_inside_a_git_checkout(tmp_path: Path) -> None:
    """The repo is public; recordings of Andrew's voice must never land in it."""
    (tmp_path / ".git").mkdir()
    with pytest.raises(evalset.PullError, match="git"):
        evalset.check_output_dir(tmp_path / "eval" / "clips")


def test_default_output_is_under_xdg_data_home(tmp_path: Path) -> None:
    out = evalset.default_output_dir({"XDG_DATA_HOME": str(tmp_path), "HOME": "/nowhere"})
    assert out == tmp_path / "jarvis-voice" / "eval"


async def test_the_eval_set_is_private_to_its_owner(tmp_path: Path) -> None:
    """Recordings of your voice: no other account on the machine may read them."""
    sid = "aaaa1111-0000-0000-0000-000000000000"
    server = FakeServer([session(sid)], {sid: [seg(0, 3_000, "One.")]})
    out = tmp_path / "eval"
    async with client(server) as c:
        await pull(c, out, max_clips=10)
    for p in [out, out / "clips"]:
        assert p.stat().st_mode & 0o777 == 0o700, p
    for f in [out / "manifest.jsonl", *(out / "clips").iterdir()]:
        assert f.stat().st_mode & 0o077 == 0, f
