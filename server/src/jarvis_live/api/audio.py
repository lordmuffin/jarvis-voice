"""Serve a session's raw PCM recording as WAV, with ``Range`` support for seeking."""

import re
import struct
import uuid
from collections.abc import Iterator
from pathlib import Path

from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from jarvis_live.protocol import BYTES_PER_MS

SAMPLE_RATE = BYTES_PER_MS * 500  # 16 kHz, 16-bit mono (protocol codec pcm16le_16k)
HEADER_LEN = 44
CHUNK = 64 * 1024
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def pcm_path(data_dir: Path, session_id: uuid.UUID, channel: str) -> Path:
    return data_dir / "sessions" / str(session_id) / f"{channel}.pcm"


def audio_available(data_dir: Path, session_id: uuid.UUID, channel: str) -> bool:
    try:
        return pcm_path(data_dir, session_id, channel).stat().st_size > 0
    except OSError:
        return False


def wav_header(data_len: int) -> bytes:
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_len)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16)
        + b"data"
        + struct.pack("<I", data_len)
    )


def _parse_range(value: str, size: int) -> tuple[int, int] | None:
    """``(start, end)`` inclusive, or None if unsatisfiable. Single ranges only."""
    m = _RANGE.match(value.strip())
    if m is None or (not m[1] and not m[2]):
        return None
    if not m[1]:  # suffix: the last N bytes
        n = int(m[2])
        return (max(size - n, 0), size - 1) if n > 0 else None
    start = int(m[1])
    end = min(int(m[2]), size - 1) if m[2] else size - 1
    return (start, end) if start <= end else None


def _body(path: Path, header: bytes, start: int, end: int) -> Iterator[bytes]:
    """Bytes ``start..end`` (inclusive) of header + PCM."""
    if start < HEADER_LEN:
        yield header[start : min(end + 1, HEADER_LEN)]
        start = HEADER_LEN
    if end < start:
        return
    with open(path, "rb") as f:
        f.seek(start - HEADER_LEN)
        left = end - start + 1
        while left > 0:
            data = f.read(min(CHUNK, left))
            if not data:
                return
            left -= len(data)
            yield data


async def wav_response(
    request: Request, data_dir: Path, session_id: uuid.UUID, channel: str
) -> Response:
    path = pcm_path(data_dir, session_id, channel)
    try:
        pcm_len = path.stat().st_size
    except OSError:
        pcm_len = 0
    if pcm_len == 0:
        raise HTTPException(404, "No recording. It was never recorded, or is past retention.")
    pcm_len -= pcm_len % 2  # a live recording may end mid-sample
    size = HEADER_LEN + pcm_len
    header = wav_header(pcm_len)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-cache",
        "Content-Disposition": f'inline; filename="{session_id}-{channel}.wav"',
    }
    start, end, status = 0, size - 1, 200
    if requested := request.headers.get("range"):
        r = _parse_range(requested, size)
        if r is None or r[0] >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        start, end, status = r[0], r[1], 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(end - start + 1)
    return StreamingResponse(
        _body(path, header, start, end), status_code=status, media_type="audio/wav", headers=headers
    )
