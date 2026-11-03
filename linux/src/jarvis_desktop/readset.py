"""Record a read eval set: you read known sentences aloud, so the reference text is exact.

The pulled set (``eval pull``) is real speech but its reference is the server's transcript, which
is often cut short; a TTS set has exact text but isn't your voice. Reading the shipped sentences
into your own mic gives both: your voice, your mic, an exact reference. Takes are trimmed of
silence and saved owner-only under ``$XDG_DATA_HOME/jarvis-voice/eval-read/``, never inside a git
checkout. A second run resumes after what is already recorded."""

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import wave
from collections.abc import Callable, Mapping
from importlib import resources
from pathlib import Path
from typing import Protocol

import numpy as np
from jarvis_live.client.capture import build_command, find_tool

from jarvis_desktop import evalset

SAMPLE_RATE = 16_000
BYTES_PER_MS = 32
FRAME_MS = 20
#: Below this (about -40 dBFS RMS) a 20 ms frame is silence, whatever the loudest frame is.
SILENCE_FLOOR = 300
#: A frame counts as speech above this share of the loudest frame's level.
RELATIVE_THRESHOLD = 0.05


class RecordError(Exception):
    pass


class Recorder(Protocol):
    def start(self) -> None: ...
    def stop(self) -> bytes: ...


def prompts() -> list[str]:
    text = resources.files("jarvis_desktop").joinpath("eval_prompts.txt").read_text()
    return [line.strip() for line in text.splitlines() if line.strip()]


def clip_id(index: int, prompt: str) -> str:
    """Stable per sentence: editing a sentence makes it a new clip, so old audio never pairs
    with new text."""
    return f"read-{index:02d}-{hashlib.sha1(prompt.encode()).hexdigest()[:6]}"


def trim_silence(pcm: bytes, *, pad_ms: int = 250) -> bytes:
    """Drops leading and trailing silence, keeping ``pad_ms`` around the speech. All silence
    gives ``b""``."""
    pcm = pcm[: len(pcm) - len(pcm) % 2]
    frame = FRAME_MS * BYTES_PER_MS // 2  # samples per frame
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    n = len(samples) // frame
    if n == 0:
        return b""
    rms = np.sqrt((samples[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
    threshold = max(SILENCE_FLOOR, RELATIVE_THRESHOLD * rms.max())
    loud = np.flatnonzero(rms >= threshold)
    if len(loud) == 0:
        return b""
    pad = pad_ms // FRAME_MS
    first = max(0, int(loud[0]) - pad) * frame
    last = min(len(samples), (int(loud[-1]) + 1 + pad) * frame)
    if int(loud[-1]) == n - 1:
        last = len(samples)  # speech runs to the end: keep the partial last frame too
    return samples[first:last].astype("<i2").tobytes()


class SubprocessRecorder:
    """Runs a recorder that writes 16 kHz mono PCM16 to stdout (the capture client's
    ``pw-record``/``parec`` command) for the length of one take."""

    def __init__(self, cmd: list[str]) -> None:
        self.cmd = cmd
        self._proc: subprocess.Popen[bytes] | None = None
        self._chunks: list[bytes] = []
        self._reader: threading.Thread | None = None

    def start(self) -> None:
        self._chunks = []
        self._proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout = self._proc.stdout
        assert stdout is not None

        def read() -> None:
            while chunk := os.read(stdout.fileno(), 65536):
                self._chunks.append(chunk)

        self._reader = threading.Thread(target=read, daemon=True)
        self._reader.start()

    def stop(self) -> bytes:
        proc, reader = self._proc, self._reader
        assert proc is not None and reader is not None, "stop() before start()"
        died = proc.poll()
        if died is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        reader.join(timeout=2)
        stderr = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
        if died not in (None, 0):
            raise RecordError(f"the recorder exited ({died}): {stderr or 'no message'}")
        pcm = b"".join(self._chunks)
        return pcm[: len(pcm) - len(pcm) % 2]


def record(
    ps: list[str],
    out: Path,
    recorder: Recorder,
    *,
    ask: Callable[[str], str],
    say: Callable[[str], None],
    mic: str | None = None,
) -> int:
    """Walks the sentences not yet recorded. Returns how many takes were kept this run."""
    evalset.check_output_dir(out)
    for d in (out, out / "clips"):
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
    manifest = out / "manifest.jsonl"
    done = _recorded_ids(manifest)
    todo = [(i, p) for i, p in enumerate(ps) if clip_id(i, p) not in done]
    if mic:
        say(f"Recording from: {mic}")
    say(f"{len(ps) - len(todo)} of {len(ps)} sentences already recorded; {len(todo)} to go.")
    kept = 0
    for n, (i, prompt) in enumerate(todo, 1):
        say(f"\n[{n}/{len(todo)}] {prompt}")
        while True:
            answer = ask("Enter to start (s to skip, q to quit): ").strip().lower()
            if answer == "q":
                return kept
            if answer == "s":
                break
            recorder.start()
            ask("Recording... Enter to stop: ")
            raw = recorder.stop()
            pcm = trim_silence(raw)
            if not pcm:
                say(_nothing_heard(raw))
                continue
            if ask(f"{len(pcm) / BYTES_PER_MS / 1000:.1f} s. Enter to keep, r to redo: ").strip():
                continue
            _save(out, manifest, clip_id(i, prompt), prompt, pcm)
            kept += 1
            break
    say(f"\nDone: {kept} kept this run.")
    return kept


def _nothing_heard(raw: bytes) -> str:
    if not any(raw):
        return (
            "Didn't hear anything: the mic sent no signal at all (pure zeros). It may be muted, "
            "unplugged, or a Bluetooth mic whose headset link isn't working. Check the mic "
            "named above, or pick another with --source."
        )
    return (
        "Didn't hear anything: the take is too quiet. Speak closer, or raise the mic's input "
        "level, and try again."
    )


def recorder_command(source: str | None, *, platform: str = sys.platform) -> list[str]:
    """A command that writes 16 kHz mono PCM16 to stdout from ``source`` (the default input if
    None): the capture client's ``parec``/``pw-record`` on Linux, ffmpeg's AVFoundation input on
    macOS, so the read set can be recorded on a Mac's built-in mic too."""
    if platform != "darwin":
        return build_command(find_tool(), source)
    if shutil.which("ffmpeg") is None:
        raise RecordError("recording on macOS needs ffmpeg: brew install ffmpeg")
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "avfoundation", "-i", f":{source or 'default'}",
        "-ac", "1", "-ar", "16000", "-f", "s16le", "-",
    ]  # fmt: skip


def describe_source(name: str | None, *, platform: str = sys.platform) -> str | None:
    """The sound server's description of a source (the default one if ``name`` is None)."""
    if platform == "darwin":
        return name or "the macOS default input"
    try:
        if name is None:
            name = subprocess.run(
                ["pactl", "get-default-source"], capture_output=True, text=True, check=True
            ).stdout.strip()
        listing = subprocess.run(
            ["pactl", "list", "sources"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return name
    current = None
    for line in listing.splitlines():
        line = line.strip()
        if line.startswith("Name:"):
            current = line.split(":", 1)[1].strip()
        elif line.startswith("Description:") and current == name:
            return f"{line.split(':', 1)[1].strip()} ({name})"
    return name


def default_output_dir(env: Mapping[str, str] = os.environ) -> Path:
    return evalset.default_output_dir(env).with_name("eval-read")


def _recorded_ids(manifest: Path) -> set[str]:
    if not manifest.exists():
        return set()
    return {json.loads(line)["id"] for line in manifest.read_text().splitlines() if line.strip()}


def _save(out: Path, manifest: Path, cid: str, prompt: str, pcm: bytes) -> None:
    rel = f"clips/{cid}.wav"
    tmp = out / f"{rel}.tmp"
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    tmp.chmod(0o600)
    tmp.replace(out / rel)
    row = {
        "id": cid,
        "session": "read",
        "audio": rel,
        "reference": prompt,
        "duration_ms": len(pcm) // BYTES_PER_MS,
        "source": "read",
    }
    fd = os.open(manifest, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as fh:
        fh.write(json.dumps(row) + "\n")
    with contextlib.suppress(OSError):
        manifest.chmod(0o600)
