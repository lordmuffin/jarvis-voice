"""End-to-end tests against the real app (uvicorn thread) and a real Postgres."""

import asyncio
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import websockets
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.auth import create_device
from jarvis_live.db.models import AudioChunk, Device, MarkerRow, SegmentRow
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.ingest.store import AudioStore
from jarvis_live.protocol import Frame, server_message_adapter
from jarvis_live.replay import ReplayProducer
from jarvis_live.retention import purge_expired_audio
from tests.audio import TWO_UTTERANCES, dominant_freq, silence, tone
from tests.conftest import FakeSTT, ServerHandle
from tests.helpers import RawProducer, eventually, open_viewer
from tests.test_segmenter import run as segment

SM = async_sessionmaker  # type: ignore[type-arg]
N_FRAMES = 77  # TWO_UTTERANCES is 7.7 s = 77 x 100 ms frames
FAST = 50.0


async def chunk_rows(sm: SM, sid: str, channel: str = "mic") -> list[AudioChunk]:
    async with sm() as db:
        return list(
            (
                await db.execute(
                    select(AudioChunk)
                    .where(AudioChunk.session_id == uuid.UUID(sid), AudioChunk.channel == channel)
                    .order_by(AudioChunk.seq)
                )
            )
            .scalars()
            .all()
        )


async def final_segments(sm: SM, sid: str) -> list[SegmentRow]:
    async with sm() as db:
        return list(
            (
                await db.execute(
                    select(SegmentRow)
                    .where(SegmentRow.session_id == uuid.UUID(sid), SegmentRow.source == "final")
                    .order_by(SegmentRow.start_ms)
                )
            )
            .scalars()
            .all()
        )


def pcm_file(server: ServerHandle, sid: str, channel: str = "mic") -> bytes:
    return (server.settings.data_dir / "sessions" / sid / f"{channel}.pcm").read_bytes()


# --- full session ------------------------------------------------------------------------------


async def test_full_session_via_replay(server: ServerHandle, token: str, sm: SM) -> None:
    rp = ReplayProducer(server.url, token, speed=FAST)
    sid = await rp.create_session("standup")
    async with await open_viewer(rp, sid) as viewer:
        res = await rp.run(TWO_UTTERANCES, session_id=sid)
        await eventually(lambda: len(viewer.of_type("segment")) == 2)

    # producer: acks cover every frame; segments arrive before the server closes
    assert res.closed_by_server
    assert res.sent_frames == N_FRAMES
    assert res.acked == {"mic": N_FRAMES - 1, "system": None}
    produced = [e for e in res.events if e["type"] == "segment"]
    assert [e["text"] for e in produced] == ["seg-1", "seg-2"]
    for e in produced:
        server_message_adapter.validate_python(e)  # conforms to the protocol
        assert (e["channel"], e["speaker"], e["stt_tier"]) == ("mic", "me", "fake")

    # durable audio: byte-exact file, contiguous index
    assert pcm_file(server, sid) == TWO_UTTERANCES
    rows = await chunk_rows(sm, sid)
    assert [r.seq for r in rows] == list(range(N_FRAMES))
    assert [r.byte_offset for r in rows] == [i * 3200 for i in range(N_FRAMES)]
    assert all(r.byte_len == 3200 for r in rows)
    assert [r.t_ms for r in rows][:3] == [0, 100, 200]

    # segments persisted with the VAD boundaries
    segs = await final_segments(sm, sid)
    expected = segment(TWO_UTTERANCES)
    assert [(s.start_ms, s.end_ms) for s in segs] == [(e.start_ms, e.end_ms) for e in expected]
    assert [s.text for s in segs] == ["seg-1", "seg-2"]
    assert {s.id.hex for s in segs} == {e["id"].replace("-", "") for e in produced}

    # the second segment was transcribed with the first one's text as prompt
    assert server.stt.prompts == ["", "seg-1"]  # type: ignore[attr-defined]

    # viewer got exactly what the producer got
    assert viewer.of_type("segment") == produced
    assert not viewer.of_type("ack") and not viewer.of_type("hello_ack")

    # REST view
    async with httpx.AsyncClient(
        base_url=server.url, headers={"Authorization": f"Bearer {token}"}
    ) as c:
        body = (await c.get(f"/v1/sessions/{sid}")).json()
    assert body["status"] == "finalizing" and body["ended_at"] is not None
    assert [s["text"] for s in body["segments"]] == ["seg-1", "seg-2"]
    assert body["copilot"]["version"] == 0 and body["copilot"]["notes"] == []


# --- resume ------------------------------------------------------------------------------------


async def test_resume_has_no_gaps_or_duplicates(server: ServerHandle, token: str, sm: SM) -> None:
    rp = ReplayProducer(server.url, token, speed=10.0)  # slow enough for acks to flow
    sid = await rp.create_session()

    # Kill the socket in the middle of the second utterance (frames 35-55).
    res = await rp.run(TWO_UTTERANCES, session_id=sid, abort_after_frames=45)
    assert not [e for e in res.events if e["type"] == "error"]
    stored_at_abort = len(await chunk_rows(sm, sid))
    assert 0 < stored_at_abort <= 45
    # the client is behind the server, so the resume below must re-send already-stored frames
    assert res.acked["mic"] is None or res.acked["mic"] < stored_at_abort - 1

    res = await rp.run(TWO_UTTERANCES, session_id=sid, resume_from=res.acked["mic"], result=res)

    rows = await chunk_rows(sm, sid)
    assert [r.seq for r in rows] == list(range(N_FRAMES)), "gap or duplicate in audio_chunks"
    assert pcm_file(server, sid) == TWO_UTTERANCES, "audio file differs from what was sent"
    assert res.acked["mic"] == N_FRAMES - 1

    # Segments are exactly what an uninterrupted stream yields: nothing lost, split or doubled.
    segs = await final_segments(sm, sid)
    expected = segment(TWO_UTTERANCES)
    assert [(s.start_ms, s.end_ms) for s in segs] == [(e.start_ms, e.end_ms) for e in expected]
    assert [s.text for s in segs] == ["seg-1", "seg-2"]
    assert segs[0].end_ms <= segs[1].start_ms


async def test_resend_of_stored_frames_is_acknowledged_and_ignored(
    server: ServerHandle, token: str, sm: SM
) -> None:
    rp = ReplayProducer(server.url, token)
    sid = await rp.create_session()
    p, hello_ack = await RawProducer.connect(rp, sid)
    assert hello_ack["acked"] == {"mic": None, "system": None}
    nxt = await p.send_pcm(0, silence(0.5))  # seq 0..4
    await p.send_pcm(0, silence(0.5), seq0=2)  # re-send 2..6: 2..4 are duplicates
    assert nxt == 5
    ack = None
    while ack is None or ack["seq"] < 6:
        ack = await p.recv_until("ack")
    await p.close()
    assert [r.seq for r in await chunk_rows(sm, sid)] == list(range(7))

    # reconnect: hello_ack reports the last stored seq
    p2, hello_ack = await RawProducer.connect(rp, sid, resume={"mic": 3, "system": None})
    assert hello_ack["acked"] == {"mic": 6, "system": None}
    await p2.close()


async def test_seq_gap_is_rejected(server: ServerHandle, token: str, sm: SM) -> None:
    rp = ReplayProducer(server.url, token)
    sid = await rp.create_session()
    p, _ = await RawProducer.connect(rp, sid)
    await p.send_pcm(0, silence(0.2), seq0=0)  # seq 0,1
    await p.send_pcm(0, silence(0.1), seq0=5)  # seq 5: gap
    err = await p.recv_until("error")
    assert err["code"] == "seq_gap"
    assert [r.seq for r in await chunk_rows(sm, sid)] == [0, 1]


@pytest.mark.parametrize(
    ("first", "code"),
    [
        ('{"type":"end"}', "hello_required"),
        ("not json", "hello_required"),
        (
            '{"type":"hello","protocol":2,"device":"x","codec":"pcm16le_16k","resume":{}}',
            "hello_required",
        ),
    ],
)
async def test_hello_must_come_first(
    server: ServerHandle, token: str, first: str, code: str
) -> None:
    rp = ReplayProducer(server.url, token)
    sid = await rp.create_session()
    ticket = await rp.ticket(sid)
    async with websockets.connect(rp._ws_url(sid, ticket)) as ws:
        await ws.send(first)
        msg = await asyncio.wait_for(ws.recv(), 5)
        assert code in str(msg)


async def test_bad_frame_and_bad_channel_are_rejected(server: ServerHandle, token: str) -> None:
    rp = ReplayProducer(server.url, token)  # mic-only session
    sid = await rp.create_session()
    p, _ = await RawProducer.connect(rp, sid)
    await p.ws.send(b"\x01" * 11)
    assert (await p.recv_until("error"))["code"] == "bad_frame"

    p, _ = await RawProducer.connect(rp, sid)
    await p.send_pcm(1, silence(0.2))  # system frame on a mic-only session
    assert (await p.recv_until("error"))["code"] == "bad_channel"


# --- viewers, tickets, auth --------------------------------------------------------------------


async def test_viewer_is_read_only(server: ServerHandle, token: str) -> None:
    rp = ReplayProducer(server.url, token)
    sid = await rp.create_session()
    ticket = await rp.ticket(sid, "viewer")
    async with websockets.connect(rp._ws_url(sid, ticket)) as ws:
        await ws.send(b"\x01" * 700)
        assert "read_only" in str(await asyncio.wait_for(ws.recv(), 5))


async def test_ticket_reuse_is_rejected(server: ServerHandle, token: str) -> None:
    rp = ReplayProducer(server.url, token)
    sid = await rp.create_session()
    ticket = await rp.ticket(sid)
    async with websockets.connect(rp._ws_url(sid, ticket)):
        pass
    with pytest.raises(websockets.exceptions.InvalidStatus) as exc:
        await websockets.connect(rp._ws_url(sid, ticket))
    assert exc.value.response.status_code == 403


async def test_ticket_is_bound_to_its_session_and_required(
    server: ServerHandle, token: str
) -> None:
    rp = ReplayProducer(server.url, token)
    a, b = await rp.create_session(), await rp.create_session()
    ticket = await rp.ticket(a)
    with pytest.raises(websockets.exceptions.InvalidStatus):
        await websockets.connect(rp._ws_url(b, ticket))
    with pytest.raises(websockets.exceptions.InvalidStatus):
        await websockets.connect(rp._ws_url(b, "garbage"))
    with pytest.raises(websockets.exceptions.InvalidStatus):
        await websockets.connect(rp._ws_url(b, ""))


async def test_rest_auth(server: ServerHandle, token: str, sm: SM) -> None:
    async with httpx.AsyncClient(base_url=server.url) as c:
        assert (await c.get("/v1/sessions")).status_code == 401
        bad = {"Authorization": "Bearer nope"}
        assert (await c.get("/v1/sessions", headers=bad)).status_code == 401
        good = {"Authorization": f"Bearer {token}"}
        assert (await c.get("/v1/sessions", headers=good)).status_code == 200

        async with sm() as db:  # revoke
            await db.execute(update(Device).values(revoked_at=func.now()))
            await db.commit()
        assert (await c.get("/v1/sessions", headers=good)).status_code == 401
        body = {"mode": "solo", "channels": ["mic"]}
        assert (await c.post("/v1/sessions", json=body, headers=good)).status_code == 401


async def test_token_is_stored_hashed(server: ServerHandle, token: str, sm: SM) -> None:
    from jarvis_live.auth import hash_token

    async with sm() as db:
        hashes = set((await db.execute(select(Device.token_hash))).scalars())
    assert hash_token(token) in hashes
    assert token not in hashes


async def test_sessions_are_private_to_their_device(
    server: ServerHandle, token: str, sm: SM
) -> None:
    rp = ReplayProducer(server.url, token)
    sid = await rp.create_session()
    _, other = await create_device(sm, "other")
    h = {"Authorization": f"Bearer {other}"}
    async with httpx.AsyncClient(base_url=server.url, headers=h) as c:
        assert (await c.get(f"/v1/sessions/{sid}")).status_code == 404
        assert (
            await c.post(f"/v1/sessions/{sid}/ticket", json={"role": "viewer"})
        ).status_code == 404
        assert (await c.post(f"/v1/sessions/{sid}/end")).status_code == 404
        assert (await c.get("/v1/sessions")).json()["total"] == 0


async def test_session_rest_lifecycle(server: ServerHandle, sm: SM) -> None:
    _, tok = await create_device(sm, "rest")
    h = {"Authorization": f"Bearer {tok}"}
    async with httpx.AsyncClient(base_url=server.url, headers=h) as c:
        ids = []
        for i in range(3):
            r = await c.post(
                "/v1/sessions",
                json={"title": f"s{i}", "mode": "meeting", "channels": ["mic", "system"]},
            )
            assert r.status_code == 201
            ids.append(r.json()["id"])
            await asyncio.sleep(0.01)

        page = (await c.get("/v1/sessions", params={"limit": 2})).json()
        assert page["total"] == 3
        assert [s["id"] for s in page["items"]] == [ids[2], ids[1]]  # newest first
        page2 = (await c.get("/v1/sessions", params={"limit": 2, "offset": 2})).json()
        assert [s["id"] for s in page2["items"]] == [ids[0]]

        s = (await c.get(f"/v1/sessions/{ids[0]}")).json()
        assert (s["status"], s["mode"], s["channels"], s["segments"]) == (
            "live",
            "meeting",
            ["mic", "system"],
            [],
        )

        assert (
            await c.post("/v1/sessions", json={"mode": "bad", "channels": ["mic"]})
        ).status_code == 422
        assert (await c.get(f"/v1/sessions/{uuid.uuid4()}")).status_code == 404

        r = await c.post(f"/v1/sessions/{ids[0]}/end")
        assert r.json()["status"] == "finalizing"
        ended_at = r.json()["ended_at"]
        r = await c.post(f"/v1/sessions/{ids[0]}/end")  # idempotent
        assert r.json()["status"] == "finalizing" and r.json()["ended_at"] == ended_at
        # an ended session no longer hands out producer tickets
        assert (
            await c.post(f"/v1/sessions/{ids[0]}/ticket", json={"role": "producer"})
        ).status_code == 409
        assert (
            await c.post(f"/v1/sessions/{ids[0]}/ticket", json={"role": "viewer"})
        ).status_code == 200


async def test_rest_end_disconnects_producer_and_drains(
    server: ServerHandle, token: str, sm: SM
) -> None:
    rp = ReplayProducer(server.url, token)
    sid = await rp.create_session()
    p, _ = await RawProducer.connect(rp, sid)
    await p.send_pcm(0, silence(0.5) + tone(1.0))  # an utterance still open when the session ends
    async with httpx.AsyncClient(
        base_url=server.url, headers={"Authorization": f"Bearer {token}"}
    ) as c:
        r = await c.post(f"/v1/sessions/{sid}/end")
    assert r.json()["status"] == "finalizing"
    # the open utterance was flushed into a segment, and the producer was cut off
    assert [s.text for s in await final_segments(sm, sid)] == ["seg-1"]
    with pytest.raises(websockets.ConnectionClosed):
        for _ in range(100):
            await p.send_pcm(0, silence(0.1), seq0=100)
            await asyncio.sleep(0.02)


# --- drafts, markers, status, echo -------------------------------------------------------------


async def test_draft_and_marker_are_persisted_and_republished(
    server: ServerHandle, token: str, sm: SM
) -> None:
    rp = ReplayProducer(server.url, token)
    sid = await rp.create_session()
    async with await open_viewer(rp, sid) as viewer:
        p, _ = await RawProducer.connect(rp, sid)
        draft = {
            "type": "draft_segment",
            "channel": "mic",
            "start_ms": 0,
            "end_ms": 900,
            "text": "hel",
            "final": False,
        }
        final = {**draft, "text": "hello", "final": True}
        marker = {"type": "marker", "t_ms": 1234, "label": "decision"}
        for m in (draft, final, marker):
            await p.send_json(m)
        await eventually(lambda: len(viewer.events) == 3)
        assert viewer.events == [draft, final, marker]
        # the producer sees the same events
        assert [(await p.recv_until(m["type"])) for m in (draft, marker)] == [draft, marker]
        await p.close()

    async with sm() as db:
        drafts = (
            (await db.execute(select(SegmentRow).where(SegmentRow.session_id == uuid.UUID(sid))))
            .scalars()
            .all()
        )
        markers = (
            (await db.execute(select(MarkerRow).where(MarkerRow.session_id == uuid.UUID(sid))))
            .scalars()
            .all()
        )
    assert [(d.source, d.text, d.speaker, d.stt_tier) for d in drafts] == [
        ("draft", "hello", "me", None)
    ]
    assert [(m.t_ms, m.label) for m in markers] == [(1234, "decision")]


async def test_status_event_reports_tier_and_lag(
    make_server: Callable[..., ServerHandle], token: str
) -> None:
    server = make_server(status_interval_s=0.2)
    rp = ReplayProducer(server.url, token, speed=FAST)
    sid = await rp.create_session()
    async with await open_viewer(rp, sid) as viewer:
        p, _ = await RawProducer.connect(rp, sid)
        # speech, then 3 s of trailing silence: nothing closes it before we look at status
        await p.send_pcm(0, silence(0.5) + tone(1.0) + silence(0.5))
        await eventually(lambda: len(viewer.of_type("status")) >= 2)
        await p.close()
    for s in viewer.of_type("status"):
        msg = server_message_adapter.validate_python(s)
        assert msg.stt_tier == "fake" and msg.llm_ok is False and msg.lag_ms >= 0  # type: ignore[union-attr]


async def test_status_lag_is_stream_time_minus_last_final_segment(
    make_server: Callable[..., ServerHandle], token: str
) -> None:
    server = make_server(status_interval_s=0.2)
    rp = ReplayProducer(server.url, token)
    sid = await rp.create_session()
    async with await open_viewer(rp, sid) as viewer:
        p, _ = await RawProducer.connect(rp, sid)
        await p.send_pcm(0, silence(0.5) + tone(1.0) + silence(1.0) + silence(2.0))  # 4.5 s
        await eventually(lambda: len(viewer.of_type("segment")) == 1)
        seg_end = viewer.of_type("segment")[0]["end_ms"]
        await eventually(
            lambda: (
                viewer.of_type("status")[-1:] != [] and viewer.of_type("status")[-1]["lag_ms"] > 0
            )
        )
        lag = viewer.of_type("status")[-1]["lag_ms"]
        await p.close()
    assert lag == 4500 - seg_end  # 4.5 s = 150 whole 30 ms frames


class ToneSTT(FakeSTT):
    async def transcribe(self, pcm: bytes, prompt: str):  # type: ignore[no-untyped-def]
        from jarvis_live.stt.backend import Transcription

        return Transcription(text=f"tone {round(dominant_freq(pcm), -1)}", tier="fake")


async def test_mic_echo_of_system_audio_is_dropped(
    make_server: Callable[..., ServerHandle], token: str, sm: SM
) -> None:
    server = make_server(stt=ToneSTT())
    rp = ReplayProducer(server.url, token, channel="system")  # creates mic+system session
    sid = await rp.create_session(mode="meeting")
    mic = silence(1) + tone(1.5, 440) + silence(1) + tone(1.5, 660) + silence(1.5)
    system = silence(1) + tone(1.5, 440) + silence(1) + tone(1.5, 880) + silence(1.5)
    assert len(mic) == len(system)
    async with await open_viewer(rp, sid) as viewer:
        p, _ = await RawProducer.connect(rp, sid)
        # interleave the channels frame by frame, as a real client does
        for i in range(0, len(mic), 3200):
            await p.send_pcm(0, mic[i : i + 3200], seq0=i // 3200)
            await p.send_pcm(1, system[i : i + 3200], seq0=i // 3200)
        await p.send_json({"type": "end"})
        await eventually(lambda: len(viewer.of_type("segment")) == 3)
        await p.close()
    got = {(s.channel, s.speaker, s.text) for s in await final_segments(sm, sid)}
    assert got == {
        ("system", "them", "tone 440"),
        ("system", "them", "tone 880"),
        ("mic", "me", "tone 660"),  # the 440 Hz mic segment duplicated the system one
    }
    assert {(e["channel"], e["text"]) for e in viewer.of_type("segment")} == {
        (c, t) for c, _, t in got
    }


# --- storage & retention -----------------------------------------------------------------------


async def _make_session(sm: SM, status: str = "live") -> uuid.UUID:
    device_id, _ = await create_device(sm, "store")
    async with sm() as db:
        row = SessionRow(device_id=device_id, mode="solo", status=status, channels=["mic"])
        db.add(row)
        await db.commit()
        return row.id


async def test_store_truncates_orphan_bytes_after_crash(sm: SM, tmp_path: Path) -> None:
    sid = await _make_session(sm)
    pcm = bytes(range(256)) * 5  # 1280 bytes
    store = AudioStore(sid, tmp_path, sm)
    await store.load()
    for seq in range(3):
        await store.append(Frame(channel=0, seq=seq, t_ms=seq * 40, pcm=pcm))
    await store.close()
    path = tmp_path / "sessions" / str(sid) / "mic.pcm"
    with path.open("ab") as f:  # crash between the file write and the index insert
        f.write(b"orphan")

    store = AudioStore(sid, tmp_path, sm)
    await store.load()
    assert store.cursor() == {"mic": 2, "system": None}
    assert path.stat().st_size == 3 * 1280
    assert await store.append(Frame(channel=0, seq=3, t_ms=120, pcm=pcm[::-1])) == "stored"
    await store.close()
    assert path.read_bytes() == pcm * 3 + pcm[::-1]
    last = (await chunk_rows(sm, str(sid)))[-1]
    assert (last.seq, last.byte_offset, last.byte_len) == (3, 3 * 1280, 1280)


async def test_fsync_is_rate_limited(
    sm: SM, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = await _make_session(sm)
    calls: list[int] = []
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd))
    store = AudioStore(sid, tmp_path, sm, fsync_interval_s=0.3)
    await store.load()
    pcm = bytes(1280)
    t0 = time.monotonic()
    seq = 0
    while time.monotonic() - t0 < 0.7:
        await store.append(Frame(channel=0, seq=seq, t_ms=0, pcm=pcm))
        seq += 1
    assert seq > 5
    assert 1 <= len(calls) <= 3  # ~ once per 0.3 s, never once per frame
    await store.close()


async def test_retention_deletes_old_pcm_of_done_sessions_only(sm: SM, tmp_path: Path) -> None:
    now = time.time()
    day = 86_400

    async def make(status: str, age_days: float) -> tuple[uuid.UUID, Path]:
        sid = await _make_session(sm, status)
        d = tmp_path / "sessions" / str(sid)
        d.mkdir(parents=True)
        f = d / "mic.pcm"
        f.write_bytes(b"x")
        os.utime(f, (now - age_days * day,) * 2)
        return sid, f

    _, old_done = await make("done", 100)
    _, new_done = await make("done", 10)
    _, old_live = await make("live", 100)
    _, old_final = await make("finalizing", 100)
    removed = await purge_expired_audio(sm, tmp_path, 90, now=now)
    assert removed == 1
    assert not old_done.exists()
    assert new_done.exists() and old_live.exists() and old_final.exists()

    # rows are kept
    async with sm() as db:
        assert (
            await db.execute(
                select(func.count()).select_from(SessionRow).where(SessionRow.status == "done")
            )
        ).scalar_one() >= 2
