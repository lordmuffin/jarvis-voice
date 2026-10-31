"""Durable, append-only store of audio frames for one session (port of Outbox.swift).

Layout under ``<root>/<session_id>/``:

- ``<channel>.pcm``         concatenated frame payloads
- ``<channel>.index.jsonl`` one ``{"seq","t_ms","offset","len"}`` object per line, appended after
                            the payload is written, so a torn write leaves at worst an unindexed
                            tail in the pcm file, which is truncated on reopen
- ``acked.json``            ``{"mic": n|null, "system": n|null}``, the cumulative server ack

Only ``mark_acked`` (driven by the server's ``ack``) frees data. Writes are not fsynced per frame:
the guarantee is survival of a process crash, not of power loss.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from jarvis_live.protocol import BYTES_PER_MS, MAX_PAYLOAD, MIN_PAYLOAD

CHANNELS = ("mic", "system")
COMPACTION_THRESHOLD_BYTES = 4 * 1024 * 1024


class OutboxError(Exception):
    pass


@dataclass(frozen=True)
class StoredFrame:
    channel: str
    seq: int
    t_ms: int
    pcm: bytes

    @property
    def duration_ms(self) -> int:
        return len(self.pcm) // BYTES_PER_MS


@dataclass
class _Entry:
    seq: int
    t_ms: int
    offset: int
    len: int


class _Channel:
    def __init__(self, pcm: BinaryIO, index: BinaryIO, entries: list[_Entry], pcm_end: int) -> None:
        self.pcm = pcm
        self.index = index
        self.entries = entries
        self.pcm_end = pcm_end


class Outbox:
    def __init__(
        self,
        session_id: str,
        root: Path,
        *,
        compaction_threshold: int = COMPACTION_THRESHOLD_BYTES,
        _create: bool = True,
    ) -> None:
        self.session_id = session_id
        self.directory = root / session_id
        self._threshold = compaction_threshold
        if _create:
            self.directory.mkdir(parents=True, exist_ok=True)
        elif not self.directory.is_dir():
            raise OutboxError(f"session {session_id} not found")
        self._acked: dict[str, int | None] = {c: None for c in CHANNELS}
        acked_file = self.directory / "acked.json"
        if acked_file.exists():
            loaded = json.loads(acked_file.read_text())
            self._acked = {c: loaded.get(c) for c in CHANNELS}
        self._channels = {c: self._open_channel(c) for c in CHANNELS}

    @classmethod
    def reopen(cls, session_id: str, root: Path, **kwargs: int) -> "Outbox":
        """Open an existing session after a crash or restart; raises if it was never created."""
        return cls(session_id, root, _create=False, **kwargs)

    # --- queries --------------------------------------------------------------------------

    def next_seq(self, channel: str) -> int:
        entries = self._state(channel).entries
        from_entries = entries[-1].seq + 1 if entries else 0
        acked = self._acked[channel]
        return max(from_entries, 0 if acked is None else acked + 1)

    def acked_seq(self, channel: str) -> int | None:
        return self._acked[channel]

    def acked_seqs(self) -> dict[str, int | None]:
        return dict(self._acked)

    def unacked(
        self, channel: str, start: int | None = None, limit: int | None = None
    ) -> list[StoredFrame]:
        """Frames the server has not acked, in seq order; ``start`` skips seqs below it."""
        state = self._state(channel)
        floor = max(start or 0, self._floor(channel))
        out: list[StoredFrame] = []
        for e in state.entries:
            if e.seq < floor:
                continue
            if limit is not None and len(out) >= limit:
                break
            state.pcm.seek(e.offset)
            pcm = state.pcm.read(e.len)
            if len(pcm) != e.len:
                raise OutboxError(f"short read at seq {e.seq} on {channel}")
            out.append(StoredFrame(channel, e.seq, e.t_ms, pcm))
        return out

    def unacked_duration_ms(self, channel: str, through: int | None = None) -> int:
        """Audio duration of unacked frames in ``(acked, through]``; None means all stored."""
        floor = self._floor(channel)
        total = 0
        for e in self._state(channel).entries:
            if e.seq < floor:
                continue
            if through is not None and e.seq > through:
                break
            total += e.len
        return total // BYTES_PER_MS

    def unacked_bytes(self) -> int:
        return sum(
            e.len for c in CHANNELS for e in self._channels[c].entries if e.seq >= self._floor(c)
        )

    # --- writes ---------------------------------------------------------------------------

    def append(self, channel: str, pcm: bytes, t_ms: int) -> StoredFrame:
        """Assign the next seq and store the frame."""
        return self.append_frame(
            channel, self.next_seq(channel) if channel in CHANNELS else 0, t_ms, pcm
        )

    def append_frame(self, channel: str, seq: int, t_ms: int, pcm: bytes) -> StoredFrame:
        state = self._state(channel)
        if not MIN_PAYLOAD <= len(pcm) <= MAX_PAYLOAD:
            raise OutboxError(f"invalid payload size {len(pcm)}")
        expected = self.next_seq(channel)
        if seq != expected:
            raise OutboxError(f"{channel}: expected {expected}, got {seq}")
        entry = _Entry(seq, t_ms, state.pcm_end, len(pcm))
        state.pcm.seek(state.pcm_end)
        state.pcm.write(pcm)
        state.pcm.flush()
        # Index after payload: an index line never points at bytes that were not written.
        state.index.seek(0, os.SEEK_END)
        state.index.write(
            json.dumps(
                {"seq": seq, "t_ms": t_ms, "offset": entry.offset, "len": entry.len},
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        state.index.flush()
        state.pcm_end += len(pcm)
        state.entries.append(entry)
        return StoredFrame(channel, seq, t_ms, pcm)

    def mark_acked(self, channel: str, seq: int) -> None:
        """Record the server's cumulative ack; lower or equal values are ignored."""
        self._state(channel)
        current = self._acked[channel]
        if current is not None and seq <= current:
            return
        self._acked[channel] = seq
        tmp = self.directory / "acked.json.tmp"
        tmp.write_text(json.dumps(self._acked))
        tmp.replace(self.directory / "acked.json")
        self._compact_if_fully_acked(channel)

    def close(self) -> None:
        for state in self._channels.values():
            state.pcm.close()
            state.index.close()

    # --- internals ------------------------------------------------------------------------

    def _state(self, channel: str) -> _Channel:
        if channel not in self._channels:
            raise OutboxError(f"unknown channel {channel!r}")
        return self._channels[channel]

    def _floor(self, channel: str) -> int:
        acked = self._acked[channel]
        return 0 if acked is None else acked + 1

    def _compact_if_fully_acked(self, channel: str) -> None:
        state = self._channels[channel]
        acked = self._acked[channel]
        if not state.entries or acked is None or acked < state.entries[-1].seq:
            return
        if state.pcm_end < self._threshold:
            return
        state.pcm.truncate(0)
        state.index.truncate(0)
        state.entries = []
        state.pcm_end = 0

    def _open_channel(self, channel: str) -> _Channel:
        pcm_path = self.directory / f"{channel}.pcm"
        index_path = self.directory / f"{channel}.index.jsonl"
        for p in (pcm_path, index_path):
            p.touch(exist_ok=True)
        pcm_size = pcm_path.stat().st_size

        # Recover: keep the longest valid, contiguous prefix of the index whose data is on disk.
        entries: list[_Entry] = []
        valid_index_bytes = 0
        data_end = 0
        cursor = 0
        raw = index_path.read_bytes()
        while (nl := raw.find(b"\n", cursor)) != -1:
            try:
                d = json.loads(raw[cursor:nl])
                entry = _Entry(d["seq"], d["t_ms"], d["offset"], d["len"])
            except (ValueError, KeyError, TypeError):
                break
            if (
                entry.offset != data_end
                or entry.offset + entry.len > pcm_size
                or (entries and entries[-1].seq + 1 != entry.seq)
            ):
                break
            entries.append(entry)
            data_end = entry.offset + entry.len
            cursor = nl + 1
            valid_index_bytes = cursor

        pcm = pcm_path.open("r+b")
        index = index_path.open("r+b")
        pcm.truncate(data_end)
        index.truncate(valid_index_bytes)
        return _Channel(pcm, index, entries, data_end)
