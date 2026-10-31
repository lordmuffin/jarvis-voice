"""The resumable producer connection, against the real app (uvicorn thread) and Postgres."""

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.client.outbox import Outbox
from jarvis_live.client.transport import ProducerConnection, ReconnectPolicy
from jarvis_live.db.models import AudioChunk
from jarvis_live.protocol import BYTES_PER_MS
from jarvis_live.replay import ReplayProducer, split_frames
from tests.audio import TWO_UTTERANCES
from tests.conftest import ServerHandle

SM = async_sessionmaker  # type: ignore[type-arg]
FAST_RETRY = ReconnectPolicy(initial_delay=0.05, max_delay=0.2)
FRAMES = split_frames(TWO_UTTERANCES)


async def _seqs(sm: SM, sid: str) -> list[int]:
    async with sm() as db:
        rows = await db.execute(
            select(AudioChunk.seq)
            .where(AudioChunk.session_id == uuid.UUID(sid), AudioChunk.channel == "mic")
            .order_by(AudioChunk.seq)
        )
        return [r[0] for r in rows]


async def _new_session(server: ServerHandle, token: str) -> str:
    return await ReplayProducer(server.url, token).create_session()


def _conn(
    server: ServerHandle, token: str, sid: str, tmp_path: Path, **kw: object
) -> ProducerConnection:
    return ProducerConnection(
        server.url,
        token,
        sid,
        Outbox(sid, tmp_path),
        device="test-linux",
        reconnect=FAST_RETRY,
        **kw,  # type: ignore[arg-type]
    )


def test_reconnect_policy_backs_off_exponentially_to_the_cap() -> None:
    p = ReconnectPolicy()
    assert [p.delay(n, random=0.0) for n in range(8)] == [0.5, 1, 2, 4, 8, 16, 30, 30]
    assert p.delay(0, random=1.0) == pytest.approx(0.625)
    assert p.delay(10, random=1.0) == 30  # jitter never exceeds the cap


async def test_streams_everything_and_ends_with_a_final_note(
    server: ServerHandle, token: str, sm: SM, tmp_path: Path
) -> None:
    sid = await _new_session(server, token)
    conn = _conn(server, token, sid, tmp_path)
    conn.start()
    for i, pcm in enumerate(FRAMES):
        conn.outbox.append("mic", pcm, t_ms=i * 100)
    conn.kick()
    assert await conn.end(timeout=30) is True
    assert conn.state == "ended"
    assert await _seqs(sm, sid) == list(range(len(FRAMES)))
    assert conn.outbox.unacked_duration_ms("mic") == 0
    assert conn.outbox.acked_seq("mic") == len(FRAMES) - 1


async def test_reconnects_and_resumes_after_the_socket_drops(
    server: ServerHandle, token: str, sm: SM, tmp_path: Path
) -> None:
    sid = await _new_session(server, token)
    conn = _conn(server, token, sid, tmp_path)
    conn.start()
    for i, pcm in enumerate(FRAMES[:40]):
        conn.outbox.append("mic", pcm, t_ms=i * 100)
    conn.kick()
    await _until(lambda: conn.outbox.acked_seq("mic") is not None)
    conn.abort_connection()  # network drop: no close handshake
    for i, pcm in enumerate(FRAMES[40:], start=40):
        conn.outbox.append("mic", pcm, t_ms=i * 100)
    conn.kick()
    assert await conn.end(timeout=30) is True
    assert await _seqs(sm, sid) == list(range(len(FRAMES))), "gap or duplicate after resume"


async def test_a_new_process_resumes_from_the_outbox(
    server: ServerHandle, token: str, sm: SM, tmp_path: Path
) -> None:
    sid = await _new_session(server, token)
    first = _conn(server, token, sid, tmp_path)
    first.start()
    for i, pcm in enumerate(FRAMES[:30]):
        first.outbox.append("mic", pcm, t_ms=i * 100)
    first.kick()
    await _until(lambda: first.outbox.acked_seq("mic") is not None)
    await first.stop()
    first.outbox.close()

    second = ProducerConnection(
        server.url,
        token,
        sid,
        Outbox.reopen(sid, tmp_path),
        device="test-linux",
        reconnect=FAST_RETRY,
    )
    second.start()
    for i, pcm in enumerate(FRAMES[30:], start=30):
        second.outbox.append("mic", pcm, t_ms=i * 100)
    second.kick()
    assert await second.end(timeout=30) is True
    assert await _seqs(sm, sid) == list(range(len(FRAMES)))


async def test_bad_token_is_fatal_not_retried(
    server: ServerHandle, token: str, tmp_path: Path
) -> None:
    sid = await _new_session(server, token)
    conn = _conn(server, "not-a-token", sid, tmp_path)
    conn.start()
    await _until(lambda: conn.state == "failed")
    assert "401" in (conn.failure or "")


async def test_unknown_session_is_fatal(server: ServerHandle, token: str, tmp_path: Path) -> None:
    conn = _conn(server, token, str(uuid.uuid4()), tmp_path)
    conn.start()
    await _until(lambda: conn.state == "failed")


async def test_end_gives_up_when_the_server_is_unreachable_and_keeps_the_outbox(
    tmp_path: Path,
) -> None:
    sid = str(uuid.uuid4())
    conn = ProducerConnection(
        "http://127.0.0.1:9", "t", sid, Outbox(sid, tmp_path), device="d", reconnect=FAST_RETRY
    )
    conn.start()
    conn.outbox.append("mic", FRAMES[0], t_ms=0)
    conn.kick()
    assert await conn.end(timeout=1) is False
    assert conn.state == "stopped"
    assert Outbox.reopen(sid, tmp_path).unacked_duration_ms("mic") == len(FRAMES[0]) // BYTES_PER_MS


async def test_server_events_are_delivered_on_the_events_queue(
    server: ServerHandle, token: str, tmp_path: Path
) -> None:
    sid = await _new_session(server, token)
    conn = _conn(server, token, sid, tmp_path)
    conn.start()
    for i, pcm in enumerate(FRAMES):
        conn.outbox.append("mic", pcm, t_ms=i * 100)
    conn.kick()
    await conn.end(timeout=30)
    seen = []
    while not conn.events.empty():
        seen.append(conn.events.get_nowait())
    assert any(e.type == "segment" for e in seen)
    assert not any(e.type in ("ack", "hello_ack") for e in seen)


async def _until(cond, timeout: float = 15.0) -> None:  # type: ignore[no-untyped-def]
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.02)
