"""Mirrors macos/JarvisLiveKit/Tests/JarvisLiveKitTests/OutboxTests.swift."""

import json
from pathlib import Path

import pytest

from jarvis_live.client.outbox import Outbox, OutboxError
from jarvis_live.protocol import BYTES_PER_MS

PCM_20MS = b"\x01\x00" * (20 * BYTES_PER_MS // 2)  # 640 bytes
PCM_100MS = b"\x02\x00" * (100 * BYTES_PER_MS // 2)  # 3200 bytes


def _fill(ob: Outbox, n: int, channel: str = "mic") -> None:
    for i in range(n):
        ob.append(channel, PCM_100MS, t_ms=i * 100)


def test_append_and_read_back(tmp_path: Path) -> None:
    ob = Outbox("s1", tmp_path)
    f0 = ob.append("mic", PCM_100MS, t_ms=0)
    f1 = ob.append("mic", PCM_20MS, t_ms=100)
    assert (f0.seq, f1.seq) == (0, 1)
    frames = ob.unacked("mic")
    assert [(f.seq, f.t_ms, f.pcm) for f in frames] == [(0, 0, PCM_100MS), (1, 100, PCM_20MS)]
    assert ob.unacked("system") == []
    assert ob.next_seq("mic") == 2
    assert ob.acked_seq("mic") is None


def test_layout_on_disk(tmp_path: Path) -> None:
    ob = Outbox("s1", tmp_path)
    _fill(ob, 2)
    ob.mark_acked("mic", 0)
    d = tmp_path / "s1"
    assert (d / "mic.pcm").stat().st_size == 2 * len(PCM_100MS)
    lines = (d / "mic.index.jsonl").read_text().splitlines()
    assert json.loads(lines[1]) == {"seq": 1, "t_ms": 100, "offset": len(PCM_100MS), "len": 3200}
    assert json.loads((d / "acked.json").read_text()) == {"mic": 0, "system": None}


def test_unacked_filters_by_ack_and_from(tmp_path: Path) -> None:
    ob = Outbox("s1", tmp_path)
    _fill(ob, 6)
    ob.mark_acked("mic", 1)
    assert [f.seq for f in ob.unacked("mic")] == [2, 3, 4, 5]
    assert [f.seq for f in ob.unacked("mic", start=4)] == [4, 5]
    assert [f.seq for f in ob.unacked("mic", limit=2)] == [2, 3]
    assert ob.unacked_duration_ms("mic") == 400
    assert ob.unacked_duration_ms("mic", through=3) == 200
    ob.mark_acked("mic", 0)  # lower ack is ignored
    assert ob.acked_seq("mic") == 1


def test_reopen_after_crash_keeps_unacked_frames(tmp_path: Path) -> None:
    ob = Outbox("s1", tmp_path)
    _fill(ob, 5)
    ob.mark_acked("mic", 1)
    ob.close()
    again = Outbox.reopen("s1", tmp_path)
    assert again.acked_seq("mic") == 1
    assert [f.seq for f in again.unacked("mic")] == [2, 3, 4]
    assert again.append("mic", PCM_100MS, t_ms=500).seq == 5


def test_recovers_from_torn_writes(tmp_path: Path) -> None:
    ob = Outbox("s1", tmp_path)
    _fill(ob, 3)
    ob.close()
    d = tmp_path / "s1"
    with (d / "mic.pcm").open("ab") as f:
        f.write(b"\x09" * 100)  # payload written, index line never was
    with (d / "mic.index.jsonl").open("ab") as f:
        f.write(b'{"seq":3,"t_ms":300,"off')  # torn index line
    again = Outbox.reopen("s1", tmp_path)
    assert [f.seq for f in again.unacked("mic")] == [0, 1, 2]
    assert (d / "mic.pcm").stat().st_size == 3 * len(PCM_100MS)
    assert again.append("mic", PCM_100MS, t_ms=300).seq == 3


def test_index_pointing_past_the_data_is_dropped(tmp_path: Path) -> None:
    ob = Outbox("s1", tmp_path)
    _fill(ob, 3)
    ob.close()
    pcm = tmp_path / "s1" / "mic.pcm"
    pcm.write_bytes(pcm.read_bytes()[: 2 * len(PCM_100MS) + 10])
    again = Outbox.reopen("s1", tmp_path)
    assert [f.seq for f in again.unacked("mic")] == [0, 1]


def test_rejects_non_contiguous_seq_and_bad_payloads(tmp_path: Path) -> None:
    ob = Outbox("s1", tmp_path)
    ob.append("mic", PCM_100MS, t_ms=0)
    with pytest.raises(OutboxError, match="expected 1"):
        ob.append_frame("mic", seq=3, t_ms=0, pcm=PCM_100MS)
    with pytest.raises(OutboxError, match="payload"):
        ob.append("mic", b"\x00" * 100, t_ms=0)  # under 20 ms
    with pytest.raises(OutboxError, match="payload"):
        ob.append("mic", b"\x00" * (201 * BYTES_PER_MS), t_ms=0)  # over 200 ms
    with pytest.raises(OutboxError, match="channel"):
        ob.append("speaker", PCM_100MS, t_ms=0)


def test_reopen_of_unknown_session_raises(tmp_path: Path) -> None:
    with pytest.raises(OutboxError, match="not found"):
        Outbox.reopen("nope", tmp_path)


def test_fully_acked_channel_is_compacted_and_sequence_continues(tmp_path: Path) -> None:
    ob = Outbox("s1", tmp_path, compaction_threshold=10_000)
    _fill(ob, 4)  # 12.8 kB > threshold
    ob.mark_acked("mic", 3)
    assert (tmp_path / "s1" / "mic.pcm").stat().st_size == 0
    assert ob.unacked("mic") == []
    assert ob.append("mic", PCM_100MS, t_ms=400).seq == 4
    ob.close()
    again = Outbox.reopen("s1", tmp_path)
    assert [f.seq for f in again.unacked("mic")] == [4]


def test_unacked_bytes_total(tmp_path: Path) -> None:
    ob = Outbox("s1", tmp_path)
    _fill(ob, 2)
    _fill(ob, 1, "system")
    assert ob.unacked_bytes() == 3 * len(PCM_100MS)
