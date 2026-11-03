"""Pull an STT evaluation set from a Jarvis Live server: your own mic audio, cut into utterances,
with the server's final transcript as the reference text.

Used to score on-device engines on a real voice and a real mic (plan section 2.2, PR 3) rather
than on TTS. The reference is the server's STT, not a human transcript, so a score against it is
agreement with the server, not true WER.

What it takes: finished sessions you own, mic channel, speaker "me", not restricted unless asked,
in the chosen modes. Only the bytes of each utterance are downloaded (HTTP Range), not whole
recordings. Output goes under ``$XDG_DATA_HOME/jarvis-voice/eval/`` and never inside a git
checkout: the repo is public and these are recordings of your voice. The device token is sent in
the ``Authorization`` header and written nowhere."""

import json
import os
import struct
import wave
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

SAMPLE_RATE = 16_000
BYTES_PER_MS = 32  # 16 kHz, mono, 16-bit
PAGE = 100  # the server's maximum page size
HEADER_PROBE = 4096


class PullError(Exception):
    pass


@dataclass(frozen=True)
class Clip:
    session_id: str
    start_ms: int
    end_ms: int
    reference: str
    stt_tier: str | None

    @property
    def id(self) -> str:
        return f"{self.session_id[:8]}-{self.start_ms:09d}"

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass
class Summary:
    clips: int = 0
    skipped: int = 0
    sessions: int = 0


def wanted(session: dict[str, Any], *, modes: set[str], include_restricted: bool) -> bool:
    return (
        session.get("status") == "done"
        and session.get("mode") in modes
        and "mic" in session.get("channels", [])
        and not session.get("local_only", False)
        and (include_restricted or not session.get("restricted", False))
    )


def plan_clips(detail: dict[str, Any], *, min_ms: int, max_ms: int, max_gap_ms: int) -> list[Clip]:
    """My mic segments, consecutive ones merged across pauses of at most ``max_gap_ms`` while the
    result stays within ``max_ms``; utterances shorter than ``min_ms`` are dropped."""
    mine = sorted(
        (
            s
            for s in detail.get("segments", [])
            if s.get("channel") == "mic" and s.get("speaker") == "me" and s.get("text", "").strip()
        ),
        key=lambda s: (s["start_ms"], s["end_ms"]),
    )
    groups: list[list[dict[str, Any]]] = []
    for s in mine:
        last = groups[-1] if groups else None
        if (
            last is not None
            and s["start_ms"] - last[-1]["end_ms"] <= max_gap_ms
            and s["end_ms"] - last[0]["start_ms"] <= max_ms
        ):
            last.append(s)
        else:
            groups.append([s])
    clips = []
    for g in groups:
        start, end = g[0]["start_ms"], g[-1]["end_ms"]
        if not min_ms <= end - start <= max_ms:
            continue
        text = " ".join(w for s in g for w in s["text"].split())
        clips.append(Clip(detail["id"], start, end, text, g[0].get("stt_tier")))
    return clips


async def pull(
    client: httpx.AsyncClient,
    out: Path,
    *,
    max_clips: int,
    modes: frozenset[str] = frozenset({"solo"}),
    include_restricted: bool = False,
    min_ms: int = 2_000,
    max_ms: int = 20_000,
    max_gap_ms: int = 600,
) -> Summary:
    """Adds up to ``max_clips`` new clips to ``out`` (``manifest.jsonl`` + ``clips/*.wav``),
    newest sessions first. Clips already in the manifest are skipped, so it can be re-run."""
    check_output_dir(out)
    for d in (out, out / "clips"):  # recordings of your voice: owner only, whatever the umask
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
    manifest = out / "manifest.jsonl"
    have = _existing_ids(manifest)
    summary = Summary()
    offset = 0
    while summary.clips < max_clips:
        page = await _get_json(client, "/v1/sessions", params={"limit": PAGE, "offset": offset})
        for s in page["items"]:
            if summary.clips >= max_clips:
                break
            if not wanted(s, modes=set(modes), include_restricted=include_restricted):
                continue
            detail = await _get_json(client, f"/v1/sessions/{s['id']}")
            clips = plan_clips(detail, min_ms=min_ms, max_ms=max_ms, max_gap_ms=max_gap_ms)
            new = [c for c in clips if c.id not in have]
            summary.skipped += len(clips) - len(new)
            if not new:
                continue
            summary.sessions += 1
            data_offset = await _data_offset(client, s["id"])
            for clip in new[: max_clips - summary.clips]:
                await _save(client, out, manifest, clip, data_offset)
                have.add(clip.id)
                summary.clips += 1
        offset += PAGE
        if offset >= page["total"]:
            break
    return summary


def check_output_dir(out: Path) -> None:
    for d in [out.resolve(), *out.resolve().parents]:
        if (d / ".git").exists():
            raise PullError(
                f"{out} is inside the git checkout {d}: recordings must never land in the repo. "
                "Use the default output directory or one outside any checkout."
            )


def default_output_dir(env: Mapping[str, str] = os.environ) -> Path:
    base = env.get("XDG_DATA_HOME") or str(Path(env.get("HOME") or Path.home()) / ".local/share")
    return Path(base) / "jarvis-voice" / "eval"


# --- helpers ---------------------------------------------------------------------------------


def _existing_ids(manifest: Path) -> set[str]:
    if not manifest.exists():
        return set()
    return {json.loads(line)["id"] for line in manifest.read_text().splitlines() if line.strip()}


async def _get(client: httpx.AsyncClient, path: str, **kw: Any) -> httpx.Response:
    r = await client.get(path, **kw)
    if r.status_code in (401, 403):
        raise PullError(f"the server rejected the device token ({r.status_code} on {path})")
    if r.status_code >= 400:
        raise PullError(f"{path}: HTTP {r.status_code}")
    return r


async def _get_json(client: httpx.AsyncClient, path: str, **kw: Any) -> dict[str, Any]:
    data: dict[str, Any] = (await _get(client, path, **kw)).json()
    return data


async def _data_offset(client: httpx.AsyncClient, session_id: str) -> int:
    """Where the PCM starts in the session's mic WAV, after checking it is 16 kHz mono PCM16."""
    head = (
        await _get(
            client,
            f"/v1/sessions/{session_id}/audio/mic.wav",
            headers={"Range": f"bytes=0-{HEADER_PROBE - 1}"},
        )
    ).content
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise PullError(f"session {session_id}: the mic audio is not a WAV file")
    pos = 12
    fmt_ok = False
    while pos + 8 <= len(head):
        chunk, size = head[pos : pos + 4], struct.unpack("<I", head[pos + 4 : pos + 8])[0]
        if chunk == b"fmt ":
            fmt, channels, rate, _, _, bits = struct.unpack("<HHIIHH", head[pos + 8 : pos + 24])
            fmt_ok = (fmt, channels, rate, bits) == (1, 1, SAMPLE_RATE, 16)
        elif chunk == b"data":
            if not fmt_ok:
                break
            return pos + 8
        pos += 8 + size + (size % 2)
    raise PullError(f"session {session_id}: expected 16 kHz mono PCM16 mic audio")


async def _save(
    client: httpx.AsyncClient, out: Path, manifest: Path, clip: Clip, data_offset: int
) -> None:
    start = data_offset + clip.start_ms * BYTES_PER_MS
    end = data_offset + clip.end_ms * BYTES_PER_MS - 1
    pcm = (
        await _get(
            client,
            f"/v1/sessions/{clip.session_id}/audio/mic.wav",
            headers={"Range": f"bytes={start}-{end}"},
        )
    ).content
    pcm = pcm[: len(pcm) - len(pcm) % 2]
    rel = Path("clips") / f"{clip.id}.wav"
    tmp = out / f"{rel}.tmp"
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    tmp.chmod(0o600)
    tmp.replace(out / rel)
    row = {
        "id": clip.id,
        "session": clip.session_id,
        "start_ms": clip.start_ms,
        "end_ms": clip.end_ms,
        "duration_ms": clip.duration_ms,
        "audio": str(rel),
        "reference": clip.reference,
        "stt_tier": clip.stt_tier,
    }
    # Appended after the WAV exists, so a crash leaves no dangling row.
    fd = os.open(manifest, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as fh:
        fh.write(json.dumps(row) + "\n")
    manifest.chmod(0o600)
