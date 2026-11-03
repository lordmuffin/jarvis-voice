"""Clips shaped like what OBS publishes: video plus one or two 48 kHz stereo AAC tracks, in
MPEG-TS (what SRT carries). Built with ffmpeg from the synthetic test audio, so nothing binary is
committed and no OBS is needed."""

import subprocess
from pathlib import Path

from tests.audio import SR, TWO_UTTERANCES, silence, tone


def _raw(path: Path, pcm: bytes) -> Path:
    path.write_bytes(pcm)
    return path


def obs_clip(
    path: Path, mic: bytes = TWO_UTTERANCES, system: bytes | None = None, *, video: bool = True
) -> Path:
    """``mic`` (and ``system``, when given) as AAC tracks 1 (and 2), like OBS's audio tracks."""
    tracks = [_raw(path.with_suffix(".mic.pcm"), mic)]
    if system is not None:
        tracks.append(_raw(path.with_suffix(".system.pcm"), system))
    seconds = len(mic) / (2 * SR)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if video:
        cmd += ["-f", "lavfi", "-i", f"color=c=black:s=64x64:r=5:d={seconds}"]
    for t in tracks:
        cmd += ["-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", str(t)]
    first_audio = 1 if video else 0
    cmd += ["-map", "0:v"] if video else []
    for i in range(len(tracks)):
        cmd += ["-map", f"{first_audio + i}:a"]
    cmd += ["-c:v", "mpeg2video"] if video else []
    cmd += ["-c:a", "aac", "-ar", "48000", "-ac", "2", "-f", "mpegts", str(path)]
    subprocess.run(cmd, check=True)  # noqa: S603
    for t in tracks:
        t.unlink()
    return path


def system_audio(seconds: float) -> bytes:
    """What the "desktop audio" track carries in tests: one 880 Hz burst (a tone the mic track
    never has) in the mic's pause at 5.5-6.5 s, then silence. It must have gaps, like real
    desktop audio: the server holds mic segments until the system channel has settled past
    them (the duplicate guard)."""
    burst = silence(5.75) + tone(0.6, freq=880.0)
    return burst + silence(seconds - len(burst) / (2 * SR))


SYSTEM_BURST_S = (5.85, 6.25)  # inside the system burst
